"""
Early Activation Agent (#11)
─────────────────────────────────────────────────────────────────────
Convierte nuevos registros en usuarios activos dentro de las 72 horas
mediante una secuencia de 5 mensajes personalizados.

Flujo:
  1. trigger(event)       — invocado al detectar user.registered
                             → inserta las 5 filas en onboarding_sequences
  2. execute_step(row)    — ejecuta un paso específico:
                             a. Verifica condición (no_application / inactive)
                             b. Obtiene contexto del usuario (vacantes, perfil)
                             c. Genera mensaje con Claude (Haiku — es generación corta)
                             d. Envía por el canal apropiado
                             e. Marca como sent/failed en la BD
                             f. Trackea en CDP + Event Bus

Uso:
    agent = EarlyActivationAgent(cdp=cdp_client, bus=event_bus, pool=pg_pool)
    await agent.trigger(ActivationEvent(user_id="...", full_name="...", ...))
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from agents.base import BaseAgent
from cdp.events import Events
from event_bus.bus import Channels

from .channels import get_channel, is_channel_configured
from .models import (
    ActivationEvent,
    Channel,
    ChannelResult,
    GeneratedMessage,
    SequenceStatus,
    StepKey,
)
from .prompts import (
    ACTIVATION_SYSTEM,
    cv_tip_prompt,
    employer_signal_prompt,
    first_apply_nudge_prompt,
    reactivation_check_prompt,
    welcome_prompt,
)
from .sequences import SEQUENCE, SEQUENCE_BY_KEY

log = structlog.get_logger(__name__)


class EarlyActivationAgent(BaseAgent):
    """
    Agente de activación temprana — secuencia de 72 horas.

    Parámetros:
        cdp:  CDPClient (para tracking + queries de estado del usuario)
        bus:  EventBus  (para publicar eventos inter-agente)
        pool: asyncpg Pool (para leer/escribir onboarding_sequences directamente)
              Si no se provee, las operaciones de BD se omiten (modo POC sin DB).
    """

    AGENT_ID = "early_activation_agent"

    def __init__(self, cdp=None, bus=None, pool=None):
        super().__init__(cdp=cdp, bus=bus)
        self._pool = pool
        self._last_generated_message = None  # caché del último mensaje generado

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    async def trigger(self, event: ActivationEvent) -> SequenceStatus:
        """
        Inicia la secuencia de 72 horas para un nuevo usuario.

        Llámalo cuando recibas el evento user.registered del Event Bus.
        Crea las 5 filas en onboarding_sequences con sus horarios.
        Si la DB no está disponible, ejecuta el paso 0 (welcome) directamente.

        Returns:
            SequenceStatus con el estado inicial de la secuencia.
        """
        await self.log_run("started", {"event": event.model_dump()}, user_id=event.user_id)
        self.log.info("activation.triggered", user_id=event.user_id, name=event.full_name)

        registered_at = event.registered_at or datetime.now(timezone.utc)

        if self._pool:
            # Insertar las 5 filas en la BD con sus horarios programados
            await self._schedule_sequence(event, registered_at)
            # El scheduler hará el resto; ejecutar welcome ahora (delay=0)
            welcome_row = await self._get_pending_step(event.user_id, StepKey.WELCOME)
            if welcome_row:
                await self.execute_step(welcome_row, event)
        else:
            # Sin DB: ejecutar welcome inmediatamente (modo POC ligero)
            self.log.warning(
                "activation.no_db",
                hint="Sin asyncpg pool — ejecutando solo el paso welcome en memoria",
            )
            await self._run_step_in_memory(StepKey.WELCOME, event)

        await self.log_run("completed", {"steps_scheduled": len(SEQUENCE)}, user_id=event.user_id)

        return SequenceStatus(
            user_id=event.user_id,
            steps_total=len(SEQUENCE),
            steps_sent=1,
            steps_pending=len(SEQUENCE) - 1,
            steps_failed=0,
            next_step=StepKey.CV_TIP,
            next_step_at=None,
            is_complete=False,
        )

    async def execute_step(
        self,
        step_row: dict[str, Any],
        event: ActivationEvent | None = None,
    ) -> ChannelResult:
        """
        Ejecuta un paso de la secuencia para un usuario.

        Parámetros:
            step_row: fila de onboarding_sequences (dict con keys: id, user_id, step, channel, metadata)
            event:    ActivationEvent del usuario (si no se pasa, lo reconstruye desde la BD)

        Returns:
            ChannelResult con success/error y message_id.
        """
        step_key  = StepKey(step_row["step"])
        user_id   = str(step_row["user_id"])
        row_id    = str(step_row["id"])
        step_conf = SEQUENCE_BY_KEY[step_key]

        self.log.info("step.executing", step=step_key, user_id=user_id)

        # ── 1. Reconstruir/obtener contexto del usuario ──────────────────────
        if event is None:
            event = await self._load_user_event(user_id)

        # ── 2. Verificar condición (no_application / inactive) ───────────────
        if not await self._check_condition(step_conf.condition, user_id):
            self.log.info("step.skipped", step=step_key, user_id=user_id, reason=step_conf.condition)
            await self._mark_step(row_id, "skipped")
            await self.track(
                Events.MESSAGE_SENT,
                user_id=user_id,
                properties={"step": step_key, "status": "skipped", "reason": step_conf.condition},
            )
            return ChannelResult(success=True, channel=step_conf.channel, message_id=None)

        # ── 3. Obtener contexto enriquecido (vacantes, datos de mercado) ─────
        context = await self._build_context(step_key, event)

        # ── 4. Generar mensaje con Claude ────────────────────────────────────
        try:
            generated = await self._generate_message(step_key, event, context)
        except Exception as exc:
            self.log.error("step.generate_failed", step=step_key, error=str(exc))
            await self._mark_step(row_id, "failed", error=str(exc))
            return ChannelResult(success=False, channel=step_conf.channel, error=str(exc))

        # ── 5. Enviar por el canal apropiado ─────────────────────────────────
        result = await self._send_message(step_key, event, generated, step_conf)

        # ── 6. Actualizar BD + CDP + Bus ─────────────────────────────────────
        status = "sent" if result.success else "failed"
        await self._mark_step(row_id, status,
                              message_id=result.message_id, error=result.error)

        await self.track(
            Events.MESSAGE_SENT,
            user_id=user_id,
            properties={
                "step":       step_key,
                "channel":    result.channel,
                "message_id": result.message_id,
                "success":    result.success,
                "subject":    generated.subject,
            },
        )

        if result.success:
            await self.publish(
                Channels.USERS,
                "activation.step_sent",
                {
                    "user_id":    user_id,
                    "step":       step_key,
                    "channel":    result.channel,
                    "message_id": result.message_id,
                },
            )

        self.log.info(
            "step.done",
            step=step_key,
            user_id=user_id,
            channel=result.channel,
            success=result.success,
        )
        return result

    async def get_status(self, user_id: str) -> SequenceStatus:
        """Retorna el estado actual de la secuencia del usuario."""
        if not self._pool:
            return SequenceStatus(
                user_id=user_id,
                steps_total=len(SEQUENCE),
                steps_sent=0, steps_pending=len(SEQUENCE),
                steps_failed=0, next_step=StepKey.WELCOME,
                next_step_at=None, is_complete=False,
            )

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT step, status FROM onboarding_sequences WHERE user_id = $1",
                uuid.UUID(user_id),
            )

        counts = {"sent": 0, "failed": 0, "skipped": 0, "pending": 0}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        steps_sent    = counts["sent"] + counts["skipped"]
        steps_failed  = counts["failed"]
        steps_pending = counts["pending"]

        return SequenceStatus(
            user_id=user_id,
            steps_total=len(SEQUENCE),
            steps_sent=steps_sent,
            steps_pending=steps_pending,
            steps_failed=steps_failed,
            next_step=None,
            next_step_at=None,
            is_complete=(steps_pending == 0 and steps_failed == 0),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PRIVATE — Scheduling & DB
    # ══════════════════════════════════════════════════════════════════════════

    async def _schedule_sequence(
        self, event: ActivationEvent, registered_at: datetime
    ) -> None:
        """Inserta las 5 filas en onboarding_sequences con horarios calculados."""
        async with self._pool.acquire() as conn:
            for step in SEQUENCE:
                from datetime import timedelta
                scheduled_at = registered_at + timedelta(hours=step.delay_hours)
                await conn.execute(
                    """
                    INSERT INTO onboarding_sequences
                        (id, user_id, step, scheduled_at, channel, status, metadata)
                    VALUES ($1, $2, $3, $4, $5, 'pending', $6)
                    ON CONFLICT DO NOTHING
                    """,
                    uuid.uuid4(),
                    uuid.UUID(event.user_id),
                    step.key.value,
                    scheduled_at,
                    step.channel.value,
                    json.dumps({"condition": step.condition}),
                )
        self.log.info(
            "sequence.scheduled",
            user_id=event.user_id,
            steps=len(SEQUENCE),
        )

    async def _get_pending_step(
        self, user_id: str, step_key: StepKey
    ) -> dict[str, Any] | None:
        """Recupera una fila pendiente específica de la secuencia."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, step, channel, status, metadata, scheduled_at
                FROM onboarding_sequences
                WHERE user_id = $1 AND step = $2 AND status = 'pending'
                LIMIT 1
                """,
                uuid.UUID(user_id),
                step_key.value,
            )
        return dict(row) if row else None

    async def _mark_step(
        self,
        row_id: str,
        status: str,
        message_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Actualiza el estado de un paso en la BD."""
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE onboarding_sequences
                SET status = $1,
                    sent_at = CASE WHEN $1 = 'sent' THEN NOW() ELSE sent_at END,
                    metadata = metadata || $2::jsonb
                WHERE id = $3
                """,
                status,
                json.dumps({
                    "message_id": message_id,
                    "error": error,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }),
                uuid.UUID(row_id),
            )

    async def _load_user_event(self, user_id: str) -> ActivationEvent:
        """Reconstruye un ActivationEvent desde la BD de usuarios."""
        if not self._pool:
            return ActivationEvent(user_id=user_id, full_name="Usuario")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, full_name, email, phone, source, city,
                       current_title, skills, experience_years, created_at
                FROM users WHERE id = $1
                """,
                uuid.UUID(user_id),
            )
        if not row:
            return ActivationEvent(user_id=user_id, full_name="Usuario")

        return ActivationEvent(
            user_id=str(row["id"]),
            full_name=row["full_name"] or "",
            email=row["email"],
            phone=row["phone"],
            source=row["source"] or "organic",
            city=row["city"] or "",
            current_title=row["current_title"] or "",
            skills=list(row["skills"] or []),
            experience_years=row["experience_years"] or 0,
            registered_at=row["created_at"],
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PRIVATE — Condition checking
    # ══════════════════════════════════════════════════════════════════════════

    async def _check_condition(self, condition: str, user_id: str) -> bool:
        """
        Evalúa la condición de un paso antes de enviarlo.

        "always"         → True siempre
        "no_application" → True si el usuario NO ha aplicado a ninguna vacante
        "inactive"       → True si el usuario NO tiene actividad significativa
        """
        if condition == "always":
            return True

        if not self._pool:
            # Sin DB: asumir que siempre aplica (modo POC)
            self.log.debug("condition.no_db", condition=condition, assuming=True)
            return True

        if condition == "no_application":
            return await self._has_no_applications(user_id)

        if condition == "inactive":
            return await self._is_inactive(user_id)

        self.log.warning("condition.unknown", condition=condition)
        return True

    async def _has_no_applications(self, user_id: str) -> bool:
        """True si el usuario no ha aplicado a ninguna vacante."""
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM applications WHERE user_id = $1",
                uuid.UUID(user_id),
            )
        return (count or 0) == 0

    async def _is_inactive(self, user_id: str) -> bool:
        """
        True si el usuario está inactivo:
        - No ha aplicado a vacantes, Y
        - profile_completion < 50%, Y
        - No tiene eventos recientes (últimas 24h) excepto el registro
        """
        async with self._pool.acquire() as conn:
            app_count = await conn.fetchval(
                "SELECT COUNT(*) FROM applications WHERE user_id = $1",
                uuid.UUID(user_id),
            )
            if (app_count or 0) > 0:
                return False

            profile_pct = await conn.fetchval(
                "SELECT profile_completion FROM users WHERE id = $1",
                uuid.UUID(user_id),
            )
            if (profile_pct or 0) >= 50:
                return False

            # Verificar eventos recientes (excluye user.registered)
            recent = await conn.fetchval(
                """
                SELECT COUNT(*) FROM events
                WHERE user_id = $1
                  AND event_type != 'user.registered'
                  AND timestamp > NOW() - INTERVAL '48 hours'
                """,
                uuid.UUID(user_id),
            )
            return (recent or 0) == 0

    # ══════════════════════════════════════════════════════════════════════════
    # PRIVATE — Context building
    # ══════════════════════════════════════════════════════════════════════════

    async def _build_context(
        self, step_key: StepKey, event: ActivationEvent
    ) -> dict[str, Any]:
        """
        Construye el contexto enriquecido para cada paso.
        Intenta obtener vacantes reales del Job Match Agent; si no está
        disponible, usa datos mock para no romper el flujo.
        """
        context: dict[str, Any] = {}

        if step_key in (StepKey.WELCOME, StepKey.FIRST_APPLY_NUDGE):
            context["top_jobs"] = await self._get_top_jobs(event)

        elif step_key == StepKey.EMPLOYER_SIGNAL:
            context["top_jobs"]    = await self._get_top_jobs(event)
            context["demand_data"] = self._mock_demand_data(event)

        elif step_key == StepKey.CV_TIP:
            context["profile_completion"] = await self._get_profile_completion(event.user_id)

        return context

    async def _get_top_jobs(self, event: ActivationEvent) -> list[dict]:
        """Obtiene las top vacantes para el usuario vía Job Match Agent o mock."""
        try:
            # Llamar al Job Match Agent internamente (sin HTTP)
            from agents.job_match.agent import JobMatchAgent
            from agents.job_match.models import JobMatchRequest

            jm_agent = JobMatchAgent(cdp=self._cdp, bus=self._bus)
            user_profile = {
                "user_id":        event.user_id,
                "full_name":      event.full_name,
                "current_title":  event.current_title,
                "city":           event.city,
                "skills":         event.skills,
                "experience_years": event.experience_years,
            }
            request = JobMatchRequest(
                user=user_profile,
                top_k=5,
                filter_city=event.city or None,
                include_remote=True,
            )
            result = await jm_agent.run(request)
            await jm_agent.close()

            return [
                {
                    "title":       j.title,
                    "company":     j.company,
                    "city":        j.city,
                    "modality":    j.modality,
                    "match_score": j.match_score,
                }
                for j in (result.matched_jobs or [])[:5]
            ]
        except Exception as exc:
            self.log.warning("context.jobs_fallback", error=str(exc))
            return self._mock_jobs(event)

    async def _get_profile_completion(self, user_id: str) -> int:
        """Lee el % de completitud del perfil desde la BD."""
        if not self._pool:
            return 35  # mock razonable para POC

        try:
            async with self._pool.acquire() as conn:
                pct = await conn.fetchval(
                    "SELECT profile_completion FROM users WHERE id = $1",
                    uuid.UUID(user_id),
                )
            return int(pct or 0)
        except Exception:
            return 35

    def _mock_demand_data(self, event: ActivationEvent) -> dict:
        """Datos de demanda del mercado — mock para POC."""
        return {
            "companies_hiring": 18,
            "new_jobs_this_week": 54,
            "avg_salary_range": "",
        }

    def _mock_jobs(self, event: ActivationEvent) -> list[dict]:
        """Vacantes mock de respaldo cuando el Job Match Agent no está disponible."""
        title = event.current_title or "profesional"
        city  = event.city or "Bogotá"
        return [
            {"title": f"Analista {title}",       "company": "Empresa A", "city": city,      "modality": "híbrido",    "match_score": 0.88},
            {"title": f"Senior {title}",          "company": "Empresa B", "city": city,      "modality": "presencial", "match_score": 0.82},
            {"title": f"Coordinador {title}",     "company": "Empresa C", "city": "Medellín","modality": "remoto",     "match_score": 0.79},
        ]

    # ══════════════════════════════════════════════════════════════════════════
    # PRIVATE — Message generation
    # ══════════════════════════════════════════════════════════════════════════

    async def _generate_message(
        self,
        step_key: StepKey,
        event: ActivationEvent,
        context: dict[str, Any],
    ) -> GeneratedMessage:
        """
        Llama a Claude para generar el mensaje del paso.
        Usa Haiku (classification task → rápido y barato) porque los mensajes
        son cortos y el system prompt ya es muy preciso.
        """
        user_dict = event.model_dump()

        # Construir el prompt del paso
        if step_key == StepKey.WELCOME:
            user_prompt = welcome_prompt(user_dict, context.get("top_jobs", []))
        elif step_key == StepKey.CV_TIP:
            user_prompt = cv_tip_prompt(user_dict, context.get("profile_completion", 35))
        elif step_key == StepKey.EMPLOYER_SIGNAL:
            user_prompt = employer_signal_prompt(
                user_dict,
                context.get("demand_data", {}),
                context.get("top_jobs", []),
            )
        elif step_key == StepKey.FIRST_APPLY_NUDGE:
            user_prompt = first_apply_nudge_prompt(user_dict, context.get("top_jobs", []))
        elif step_key == StepKey.REACTIVATION_CHECK:
            user_prompt = reactivation_check_prompt(user_dict)
        else:
            raise ValueError(f"Unknown step_key: {step_key}")

        raw = await self.llm(
            task_type="generation",
            system=ACTIVATION_SYSTEM,
            user_message=user_prompt,
            max_tokens=600,
            temperature=0.4,
        )

        return self._parse_generated(raw, step_key)

    def _parse_generated(self, raw: str, step_key: StepKey) -> GeneratedMessage:
        """
        Parsea la respuesta JSON del LLM.
        Maneja JSON envuelto en markdown (```json...```) y fallbacks.
        """
        # Intentar extraer JSON
        text = raw.strip()
        if text.startswith("```"):
            # Remover bloque markdown
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Buscar objeto JSON dentro del texto
            import re
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}

        step_conf = SEQUENCE_BY_KEY[step_key]

        subject       = data.get("subject", step_conf.subject)
        email_body    = data.get("email_body", raw[:300])
        whatsapp_text = data.get("whatsapp_text", email_body[:160])

        return GeneratedMessage(
            step_key=step_key,
            channel=step_conf.channel,
            subject=subject,
            body=email_body,
            whatsapp_text=whatsapp_text,
            metadata={"raw_response": raw[:500]},
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PRIVATE — Channel sending
    # ══════════════════════════════════════════════════════════════════════════

    async def _send_message(
        self,
        step_key: StepKey,
        event: ActivationEvent,
        msg: GeneratedMessage,
        step_conf,
    ) -> ChannelResult:
        """
        Envía el mensaje por el canal apropiado con fallback automático.

        Lógica de fallback:
          1. Si el canal primario no está configurado (e.g. WhatsApp sin token),
             se usa el fallback_channel directamente — sin pasar por LogChannel.
          2. Si el canal primario está configurado pero falla en el envío,
             se intenta con el fallback_channel.
        """
        channel_instance = get_channel(step_conf.channel)

        # ── Fallback anticipado si el canal primario no está disponible ─────
        # Nota: channel_instance puede ser LogChannel aunque el canal pedido
        # fuera WHATSAPP (get_channel hace el swap internamente). Por eso
        # se verifica el canal ORIGINAL con is_channel_configured(), no la
        # instancia devuelta.
        using_fallback = False
        if (
            not is_channel_configured(step_conf.channel)
            and step_conf.fallback_channel != step_conf.channel
        ):
            self.log.info(
                "channel.fallback_early",
                original=step_conf.channel.value,
                fallback=step_conf.fallback_channel.value,
                reason="canal primario no configurado",
            )
            channel_instance = get_channel(step_conf.fallback_channel)
            using_fallback = True

        # ── Seleccionar to/body según el canal efectivo ──────────────────────
        effective_channel = step_conf.fallback_channel if using_fallback else step_conf.channel

        if effective_channel == Channel.EMAIL:
            to   = event.email or ""
            body = msg.body
        elif effective_channel == Channel.WHATSAPP:
            to   = event.phone or ""
            body = msg.whatsapp_text or msg.body
        else:
            to   = event.email or event.phone or event.user_id
            body = msg.body

        result = await channel_instance.send(to=to, subject=msg.subject, body=body)
        if using_fallback:
            result.fallback_used = True

        # ── Fallback reactivo si el canal configurado falla en el envío ──────
        if not result.success and not using_fallback and step_conf.fallback_channel != step_conf.channel:
            self.log.warning(
                "channel.fallback_reactive",
                original=step_conf.channel.value,
                fallback=step_conf.fallback_channel.value,
                reason="error en envío",
            )
            fallback = get_channel(step_conf.fallback_channel)
            to   = event.email or event.phone or ""
            body = msg.body or msg.whatsapp_text
            result = await fallback.send(to=to, subject=msg.subject, body=body)
            result.fallback_used = True

        return result

    # ══════════════════════════════════════════════════════════════════════════
    # PRIVATE — In-memory execution (modo POC sin DB)
    # ══════════════════════════════════════════════════════════════════════════

    async def _run_step_in_memory(
        self, step_key: StepKey, event: ActivationEvent
    ) -> ChannelResult:
        """
        Ejecuta un paso sin persistencia en BD.
        Útil para el modo demo/POC donde no hay PostgreSQL disponible.

        El mensaje generado queda cacheado en self._last_generated_message
        para que el demo pueda mostrarlo sin necesidad de un segundo LLM call.
        """
        step_conf = SEQUENCE_BY_KEY[step_key]
        context   = await self._build_context(step_key, event)

        try:
            generated = await self._generate_message(step_key, event, context)
            self._last_generated_message = generated  # caché para el demo
        except Exception as exc:
            self.log.error("step.in_memory.failed", step=step_key, error=str(exc))
            self._last_generated_message = None
            return ChannelResult(success=False, channel=step_conf.channel, error=str(exc))

        result = await self._send_message(step_key, event, generated, step_conf)

        await self.track(
            Events.MESSAGE_SENT,
            user_id=event.user_id,
            properties={
                "step":    step_key,
                "channel": result.channel,
                "success": result.success,
            },
        )
        return result
