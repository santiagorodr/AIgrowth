"""
Employer Signal Agent
──────────────────────────────────────────────────────────────────────────
Notifica al candidato cuando una empresa ve su perfil en elempleo.com.

Flujo:
  1. Lee eventos `employer.viewed_profile` del CDP (últimos 15 min)
  2. Filtra los que ya fueron notificados en 24h (deduplicación)
  3. Genera notificación corta y motivadora con Haiku
  4. Envía por canal + trackea `employer.signal_notified` en CDP

POC: incluye `simulate_employer_views()` para generar señales mock
     ya que no hay integración real con el portal de elempleo.com.

Uso:
    agent = EmployerSignalAgent(cdp=cdp, pool=pool)
    await agent.simulate_employer_views(n=5)  # generar señales mock
    result = await agent.process_pending()    # procesar y notificar
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone

import structlog

from agents.base import BaseAgent
from agents.early_activation.channels import get_channel
from agents.early_activation.models import Channel
from agents.employer_signal.models import BatchSignalResult, SignalResult
from agents.employer_signal.prompts import EMPLOYER_SIGNAL_SYSTEM, employer_signal_prompt
from cdp.events import Events

log = structlog.get_logger(__name__)

# Empresas y vacantes mock para el simulador
_MOCK_COMPANIES = [
    {"name": "Rappi",        "category": "tecnologia",  "job": "Desarrollador Backend"},
    {"name": "Bancolombia",  "category": "finanzas",    "job": "Analista de Datos"},
    {"name": "Grupo Éxito",  "category": "retail",      "job": "Analista de Marketing"},
    {"name": "Avianca",      "category": "transporte",  "job": "Analista de Operaciones"},
    {"name": "Platzi",       "category": "educacion",   "job": "Desarrollador Full Stack"},
    {"name": "Nequi",        "category": "fintech",     "job": "Desarrollador iOS"},
    {"name": "Adidas Colombia", "category": "retail",   "job": "Coordinador de Marketing"},
    {"name": "Sura",         "category": "seguros",     "job": "Analista de Riesgos"},
]


class EmployerSignalAgent(BaseAgent):
    """
    Agente que notifica a candidatos cuando empresas ven su perfil.

    Parámetros:
        cdp:  CDPClient (para leer/escribir eventos y simular señales)
        pool: asyncpg.Pool para queries directas
    """

    AGENT_ID = "employer_signal"

    def __init__(self, cdp=None, bus=None, pool=None):
        super().__init__(cdp=cdp, bus=bus)
        self._pool = pool

    # ── Procesamiento ──────────────────────────────────────────────────────────

    async def process_pending(self, window_minutes: int = 15) -> BatchSignalResult:
        """Procesa señales de los últimos N minutos."""
        if not self._pool:
            self.log.warning("employer_signal.no_pool")
            return BatchSignalResult.empty()

        signals = await self._get_pending_signals(window_minutes)
        if not signals:
            self.log.info("employer_signal.no_signals", window_min=window_minutes)
            return BatchSignalResult.empty()

        self.log.info("employer_signal.batch_start", signals=len(signals))
        results: list[SignalResult] = []
        skipped = 0

        for row in signals:
            user_id = str(row["user_id"])
            props   = row.get("properties") or {}
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except Exception:
                    props = {}

            company = props.get("company_name", "Una empresa")

            try:
                if await self._is_already_notified(user_id, company):
                    skipped += 1
                    continue

                user = await self._get_user_profile(user_id)
                if not user:
                    continue

                view = {
                    "company_name":          company,
                    "company_category":      props.get("company_category", ""),
                    "job_title_viewed":      props.get("job_title_viewed", ""),
                    "view_duration_seconds": props.get("view_duration_seconds", 0),
                }

                text    = await self._generate_notification(user, view)
                success, message_id = await self._send(user, view, text)

                await self.track(
                    event_type=Events.EMPLOYER_SIGNAL_SENT,
                    user_id=user_id,
                    properties={
                        "company_name":     company,
                        "job_title_viewed": view["job_title_viewed"],
                        "message_id":       message_id,
                        "success":          success,
                    },
                )

                results.append(SignalResult(
                    user_id=user_id,
                    full_name=user.get("full_name", ""),
                    company_name=company,
                    channel="log",
                    success=success,
                    message_id=message_id,
                    notification_preview=text[:80],
                ))

            except Exception as exc:
                self.log.error("employer_signal.user_error", user_id=user_id, error=str(exc))

        result = BatchSignalResult(
            total_processed=len(signals),
            sent_ok=sum(1 for r in results if r.success),
            skipped=skipped,
            results=results,
            cost_usd=len(results) * 0.001,
        )
        self.log.info("employer_signal.batch_done",
                      sent=result.sent_ok, skipped=result.skipped)
        return result

    # ── Simulador (POC) ────────────────────────────────────────────────────────

    async def simulate_employer_views(self, n: int = 5) -> int:
        """
        Genera N eventos mock `employer.viewed_profile` en el CDP.
        Simula que empresas del mock data visitaron perfiles de candidatos.
        Requiere CDP conectado para insertar los eventos.
        """
        if not self._cdp:
            self.log.warning("employer_signal.simulate.no_cdp")
            return 0
        if not self._pool:
            self.log.warning("employer_signal.simulate.no_pool")
            return 0

        # Obtener usuarios reales de la BD
        async with self._pool.acquire() as conn:
            users = await conn.fetch(
                "SELECT id, full_name, city, current_title FROM users "
                "WHERE is_active = TRUE ORDER BY RANDOM() LIMIT $1", n
            )

        count = 0
        for user in users:
            company_info = random.choice(_MOCK_COMPANIES)
            await self._cdp.track(
                event_type=Events.EMPLOYER_VIEWED_PROFILE,
                user_id=str(user["id"]),
                agent_id=self.AGENT_ID,
                properties={
                    "company_name":          company_info["name"],
                    "company_category":      company_info["category"],
                    "job_title_viewed":      company_info["job"],
                    "view_duration_seconds": random.randint(30, 180),
                    "simulated":             True,
                },
            )
            count += 1
            self.log.debug("employer_signal.simulated",
                           user=user["full_name"], company=company_info["name"])

        self.log.info("employer_signal.simulate_done", created=count)
        return count

    # ── Privados ───────────────────────────────────────────────────────────────

    async def _get_pending_signals(self, window_minutes: int) -> list[dict]:
        """Lee señales de los últimos N minutos no procesadas aún."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (user_id, properties->>'company_name')
                    user_id, properties, timestamp AS viewed_at
                FROM events
                WHERE event_type = 'employer.viewed_profile'
                  AND timestamp > NOW() - INTERVAL '1 minute' * $1
                  AND NOT EXISTS (
                    SELECT 1 FROM events e2
                    WHERE e2.user_id = events.user_id
                      AND e2.event_type = 'employer.signal_notified'
                      AND e2.properties->>'company_name' = events.properties->>'company_name'
                      AND e2.timestamp > NOW() - INTERVAL '24 hours'
                  )
                ORDER BY user_id, properties->>'company_name', timestamp DESC
                LIMIT 30
                """,
                window_minutes,
            )
        return [dict(r) for r in rows]

    async def _is_already_notified(self, user_id: str, company: str) -> bool:
        if not self._pool:
            return False
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM events
                    WHERE user_id = $1::uuid
                      AND event_type = 'employer.signal_notified'
                      AND properties->>'company_name' = $2
                      AND timestamp > NOW() - INTERVAL '24 hours'
                )
                """,
                user_id, company,
            )
        return bool(result)

    async def _get_user_profile(self, user_id: str) -> dict | None:
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1::uuid", user_id)
        return dict(row) if row else None

    async def _generate_notification(self, user: dict, view: dict) -> str:
        return await self.llm(
            task_type="classification",   # Haiku
            system=EMPLOYER_SIGNAL_SYSTEM,
            user_message=employer_signal_prompt(user, view),
            max_tokens=100,
            temperature=0.5,
        )

    async def _send(self, user: dict, view: dict, text: str) -> tuple[bool, str | None]:
        channel = get_channel(Channel.EMAIL)
        company = view.get("company_name", "Una empresa")
        result  = await channel.send(
            to=user.get("email", ""),
            subject=f"¡{company} revisó tu perfil en elempleo!",
            body=text,
        )
        return result.success, result.message_id
