"""
Re-engagement Agent
──────────────────────────────────────────────────────────────────────────
Reactiva usuarios en riesgo de abandono detectados por el Churn Predictor.

Flujo:
  1. Lee eventos `churn.risk_detected` (HIGH/MEDIUM) del CDP
  2. Filtra usuarios que NO recibieron mensaje en las últimas 72h (deduplicación)
  3. Obtiene perfil completo del usuario desde PostgreSQL
  4. Genera mensaje personalizado con Claude Sonnet
  5. Envía por canal (LogChannel en POC, Email/WhatsApp en producción)
  6. Escribe evento `reengagement.message_sent` al CDP

Uso:
    agent = ReengagementAgent(cdp=cdp, pool=pool)
    result = await agent.process_pending()
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog

from agents.base import BaseAgent
from agents.early_activation.channels import get_channel
from agents.early_activation.models import Channel
from agents.reengagement.models import (
    BatchSendResult,
    ReengagementMessage,
    SendResult,
)
from agents.reengagement.prompts import REENGAGEMENT_SYSTEM, reengagement_prompt
from cdp.events import Events

log = structlog.get_logger(__name__)

# Sólo reactivar usuarios de alto o medio riesgo
ACTIONABLE_RISK_LEVELS = {"high", "medium"}


class ReengagementAgent(BaseAgent):
    """
    Agente que genera y envía mensajes de reactivación personalizados.

    Parámetros:
        cdp:  CDPClient — para trackear eventos (opcional)
        bus:  EventBus stub — no usado en Fase 2
        pool: asyncpg.Pool — para consultas directas a events/users
    """

    AGENT_ID = "reengagement_agent"

    def __init__(self, cdp=None, bus=None, pool=None):
        super().__init__(cdp=cdp, bus=bus)
        self._pool = pool

    # ── Procesamiento batch ────────────────────────────────────────────────────

    async def process_pending(self, limit: int = 50) -> BatchSendResult:
        """
        Procesa todos los usuarios con churn detectado que aún no
        recibieron mensaje de reactivación en las últimas 72 horas.
        """
        if not self._pool:
            self.log.warning("reengagement.no_pool", msg="Sin pool — retornando batch vacío")
            return BatchSendResult.empty()

        pending = await self._get_pending_users(limit=limit)

        if not pending:
            self.log.info("reengagement.no_pending")
            return BatchSendResult.empty()

        self.log.info("reengagement.batch_start", users=len(pending))

        start    = time.time()
        results: list[SendResult] = []

        for row in pending:
            user_id    = str(row["user_id"])
            churn_data = row.get("properties") or {}
            if isinstance(churn_data, str):
                try:
                    churn_data = json.loads(churn_data)
                except json.JSONDecodeError:
                    churn_data = {}

            try:
                result = await self.process_user(user_id=user_id, churn_data=churn_data)
                results.append(result)
            except Exception as exc:
                self.log.error("reengagement.user_error", user_id=user_id, error=str(exc))
                results.append(SendResult(
                    user_id=user_id,
                    full_name="",
                    risk_level=churn_data.get("risk_level", "unknown"),
                    channel="log",
                    success=False,
                    error=str(exc),
                ))

        batch = BatchSendResult(
            total_processed=len(results),
            sent_ok=sum(1 for r in results if r.success),
            sent_failed=sum(1 for r in results if not r.success),
            results=results,
            duration_seconds=round(time.time() - start, 2),
        )

        self.log.info(
            "reengagement.batch_done",
            total=batch.total_processed,
            ok=batch.sent_ok,
            failed=batch.sent_failed,
            duration_s=batch.duration_seconds,
        )
        return batch

    # ── Procesamiento individual ───────────────────────────────────────────────

    async def process_user(self, user_id: str, churn_data: dict) -> SendResult:
        """
        Genera y envía un mensaje de reactivación a un usuario específico.

        Args:
            user_id:    UUID del usuario
            churn_data: dict con risk_level, risk_reason, days_inactive, key_signals
        """
        await self.log_run("started", user_id=user_id)

        risk_level = churn_data.get("risk_level", "medium").lower()

        # 1. Perfil completo del usuario
        user = await self._get_user_profile(user_id)
        if not user:
            self.log.warning("reengagement.user_not_found", user_id=user_id)
            return SendResult(
                user_id=user_id,
                full_name="",
                risk_level=risk_level,
                channel="log",
                success=False,
                error="Usuario no encontrado en la base de datos",
            )

        # 2. Generar mensaje con Sonnet
        message = await self._generate_message(user, churn_data)

        # 3. Enviar por canal
        channel_result = await self._send(user, message)

        # 4. Trackear evento en CDP
        await self.track(
            event_type=Events.REENGAGEMENT_SENT,
            user_id=user_id,
            properties={
                "channel":      channel_result.channel.value if hasattr(channel_result.channel, "value") else str(channel_result.channel),
                "risk_level":   risk_level,
                "message_id":   channel_result.message_id,
                "subject":      message.subject,
                "success":      channel_result.success,
            },
        )

        await self.log_run("completed", user_id=user_id)

        return SendResult(
            user_id=user_id,
            full_name=user.get("full_name", ""),
            risk_level=risk_level,
            channel=channel_result.channel.value if hasattr(channel_result.channel, "value") else str(channel_result.channel),
            success=channel_result.success,
            message_id=channel_result.message_id,
            error=channel_result.error,
            subject_preview=message.subject[:60],
        )

    # ── Privados ───────────────────────────────────────────────────────────────

    async def _get_pending_users(self, limit: int = 50) -> list[dict]:
        """
        Retorna usuarios con churn detectado (HIGH/MEDIUM) que no recibieron
        mensaje de reactivación en las últimas 72 horas.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (e1.user_id)
                    e1.user_id,
                    e1.properties,
                    e1.timestamp AS churn_detected_at
                FROM events e1
                WHERE e1.event_type = 'churn.risk_detected'
                  AND e1.properties->>'risk_level' IN ('high', 'medium')
                  AND e1.timestamp > NOW() - INTERVAL '7 days'
                  AND NOT EXISTS (
                    SELECT 1 FROM events e2
                    WHERE e2.user_id = e1.user_id
                      AND e2.event_type = 'reengagement.message_sent'
                      AND e2.timestamp > NOW() - INTERVAL '72 hours'
                  )
                ORDER BY e1.user_id, e1.timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def _get_user_profile(self, user_id: str) -> dict | None:
        """Obtiene el perfil completo del usuario desde la tabla users."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE id = $1::uuid", user_id
            )
        return dict(row) if row else None

    async def _generate_message(
        self, user: dict, churn_data: dict
    ) -> ReengagementMessage:
        """Llama a Claude Sonnet para generar el mensaje personalizado."""
        raw = await self.llm(
            task_type="generation",
            system=REENGAGEMENT_SYSTEM,
            user_message=reengagement_prompt(user, churn_data),
            max_tokens=600,
            temperature=0.5,
        )
        return self._parse_message(raw, user, churn_data)

    def _parse_message(
        self, raw: str, user: dict, churn_data: dict
    ) -> ReengagementMessage:
        """
        Parsea el JSON de Sonnet.
        Si falla, genera un mensaje de fallback genérico.
        """
        try:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON encontrado")

            data = json.loads(raw[start:end])

            return ReengagementMessage(
                subject=data.get("subject", "Te echamos de menos en elempleo"),
                email_body=data.get("email_body", ""),
                whatsapp_text=data.get("whatsapp_text", ""),
                tone=data.get("tone", "empático"),
            )

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            self.log.warning("reengagement.parse_error", error=str(exc), raw=raw[:200])

            name  = user.get("full_name", "").split()[0] if user.get("full_name") else "Hola"
            city  = user.get("city", "")
            title = user.get("current_title", "tu área")

            return ReengagementMessage(
                subject=f"{name}, hay vacantes de {title} esperándote",
                email_body=(
                    f"Hola {name},\n\n"
                    f"Hace varios días que no te vemos en elempleo.com y queremos saber cómo estás.\n\n"
                    f"Tenemos nuevas oportunidades de {title} {'en ' + city if city else ''} que podrían interesarte.\n\n"
                    f"¿Te animas a echarles un vistazo?\n\n"
                    f"El equipo de elempleo"
                ),
                whatsapp_text=(
                    f"Hola {name} 👋 Hace tiempo que no nos visitas. "
                    f"Hay vacantes nuevas de {title} que podrían ser para ti. "
                    f"¡Entra a verlas!"
                ),
                tone="empático",
            )

    async def _send(self, user: dict, message: ReengagementMessage):
        """Envía el mensaje por el canal apropiado (email con fallback a log)."""
        channel = get_channel(Channel.EMAIL)
        return await channel.send(
            to=user.get("email", ""),
            subject=message.subject,
            body=message.email_body,
        )
