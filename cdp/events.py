"""
CDP Events Client
─────────────────
Escribe eventos en PostgreSQL Y los publica al Event Bus (Redis)
de forma simultánea. Todos los agentes usan este cliente para
trackear lo que ocurre en el ecosistema.

Uso:
    from cdp.events import track

    await track("user.registered", user_id="...", properties={
        "source": "whatsapp",
        "city": "Bogotá"
    })
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger(__name__)


# ── Definición de eventos estándar ─────────────────────────────────────────
class Events:
    """Catálogo de todos los event_types del ecosistema."""

    # Usuario
    USER_REGISTERED          = "user.registered"
    USER_LOGGED_IN           = "user.logged_in"
    USER_PROFILE_UPDATED     = "user.profile_updated"
    USER_BECAME_INACTIVE     = "user.became_inactive"
    USER_REACTIVATED         = "user.reactivated"

    # Vacantes
    JOB_VIEWED               = "job.viewed"
    JOB_APPLIED              = "job.applied"
    JOB_SAVED                = "job.saved"

    # WhatsApp
    WHATSAPP_MSG_RECEIVED    = "whatsapp.message_received"
    WHATSAPP_MSG_SENT        = "whatsapp.message_sent"
    WHATSAPP_REGISTERED      = "whatsapp.user_registered"

    # Email
    EMAIL_SENT               = "email.sent"
    EMAIL_OPENED             = "email.opened"
    EMAIL_CLICKED            = "email.clicked"

    # Activación temprana
    ACTIVATION_STEP_SENT     = "activation.step_sent"
    ACTIVATION_STEP_SKIPPED  = "activation.step_skipped"
    ACTIVATION_STEP_FAILED   = "activation.step_failed"
    MESSAGE_SENT             = "message.sent"       # alias genérico multicanal

    # Agentes
    AGENT_TRIGGERED          = "agent.triggered"
    AGENT_COMPLETED          = "agent.completed"
    AGENT_ERROR              = "agent.error"

    # Churn (Fase 2)
    CHURN_RISK_DETECTED      = "churn.risk_detected"

    # Re-engagement (Fase 2)
    REENGAGEMENT_SENT        = "reengagement.message_sent"

    # Matching Notifier (Fase 2)
    MATCH_NOTIFICATION_SENT  = "match.notification_sent"

    # Profile Optimizer (Fase 2)
    PROFILE_OPTIMIZATION_SENT = "profile.optimization_suggested"

    # Employer Signal (Fase 2)
    EMPLOYER_VIEWED_PROFILE   = "employer.viewed_profile"    # evento de entrada (externo)
    EMPLOYER_SIGNAL_SENT      = "employer.signal_notified"   # evento de salida (notificación)

    # Inteligencia
    TREND_DETECTED           = "trend.detected"
    DEMAND_SIGNAL            = "demand.signal"
    EXPERIMENT_RESULT        = "experiment.result"

    # Referidos
    REFERRAL_CREATED         = "referral.created"
    REFERRAL_CONVERTED       = "referral.converted"


# ── Canales Redis (a qué canal se publica cada evento) ─────────────────────
EVENT_CHANNEL_MAP: dict[str, str] = {
    Events.USER_REGISTERED:       "users",
    Events.USER_BECAME_INACTIVE:  "retention",
    Events.USER_REACTIVATED:      "retention",
    Events.JOB_APPLIED:           "conversions",
    Events.WHATSAPP_MSG_RECEIVED: "whatsapp",
    Events.TREND_DETECTED:        "intelligence",
    Events.DEMAND_SIGNAL:         "intelligence",
    Events.EXPERIMENT_RESULT:     "intelligence",
    Events.REFERRAL_CONVERTED:    "growth",
}
DEFAULT_CHANNEL = "general"


class CDPClient:
    """
    Cliente del CDP. Mantiene conexión a PostgreSQL (Supabase).
    Instanciar una vez al arrancar la app y reutilizar.
    Event Bus eliminado — arquitectura polling sobre PostgreSQL.
    """

    def __init__(self, postgres_url: str):
        self.postgres_url = postgres_url
        self._pg_pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pg_pool = await asyncpg.create_pool(
            self.postgres_url,
            min_size=2,
            max_size=10,
        )
        log.info("cdp.connected", postgres=self.postgres_url)

    async def close(self) -> None:
        if self._pg_pool:
            await self._pg_pool.close()

    # ── track ───────────────────────────────────────────────────────────────
    async def track(
        self,
        event_type: str,
        user_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """
        Registra un evento:
          1. Lo inserta en la tabla `events` de PostgreSQL (fuente de verdad).
          2. Lo publica en el canal Redis correspondiente (tiempo real).

        Retorna el UUID del evento creado.
        """
        event_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc)
        props = properties or {}

        payload = {
            "id": event_id,
            "event_type": event_type,
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "properties": props,
            "timestamp": ts.isoformat(),
        }

        # Persistir en PostgreSQL (fuente de verdad)
        await self._persist(payload)
        await self._publish(None, payload)  # no-op, Event Bus eliminado

        log.debug(
            "event.tracked",
            event_type=event_type,
            user_id=user_id,
            agent_id=agent_id,
        )
        return event_id

    # ── Helpers ─────────────────────────────────────────────────────────────
    async def _persist(self, payload: dict) -> None:
        if not self._pg_pool:
            raise RuntimeError("CDPClient no está conectado. Llama a connect() primero.")
        async with self._pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO events
                    (id, user_id, session_id, agent_id, event_type, properties, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                payload["id"],
                payload["user_id"],
                payload["session_id"],
                payload["agent_id"],
                payload["event_type"],
                json.dumps(payload["properties"]),
                datetime.fromisoformat(payload["timestamp"]),
            )

    async def _publish(self, channel: str, payload: dict) -> None:
        pass  # Event Bus eliminado — arquitectura polling sobre PostgreSQL

    # ── Queries de utilidad ─────────────────────────────────────────────────
    async def get_user_events(
        self, user_id: str, limit: int = 50
    ) -> list[dict]:
        """Últimos N eventos de un usuario (para contexto de agentes)."""
        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_type, properties, timestamp
                FROM events
                WHERE user_id = $1
                ORDER BY timestamp DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def get_inactive_users(self, days_inactive: int = 30) -> list[dict]:
        """Usuarios sin actividad en los últimos N días (para Dormant Agent)."""
        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, email, phone, full_name, last_active_at
                FROM users
                WHERE is_active = TRUE
                  AND last_active_at < NOW() - INTERVAL '1 day' * $1
                ORDER BY last_active_at ASC
                """,
                days_inactive,
            )
        return [dict(r) for r in rows]

    async def log_agent_call(
        self,
        agent_id: str,
        model_used: str,
        task_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        success: bool = True,
        error_message: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Registra una llamada LLM para tracking de costos."""
        # Precios aproximados Claude (USD por 1M tokens)
        COST_PER_M = {
            "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
            "claude-haiku-4-5-20251001": {"input": 0.25, "output": 1.25},
        }
        prices = COST_PER_M.get(model_used, {"input": 3.0, "output": 15.0})
        cost = (prompt_tokens * prices["input"] + completion_tokens * prices["output"]) / 1_000_000

        async with self._pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_logs
                    (agent_id, task_type, model_used, prompt_tokens, completion_tokens,
                     total_tokens, cost_usd, latency_ms, success, error_message, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                agent_id,
                task_type,
                model_used,
                prompt_tokens,
                completion_tokens,
                prompt_tokens + completion_tokens,
                cost,
                latency_ms,
                success,
                error_message,
                json.dumps(metadata or {}),
            )


# ── Singleton global (inicializado en startup de cada servicio) ─────────────
_cdp: CDPClient | None = None


def get_cdp() -> CDPClient:
    if _cdp is None:
        raise RuntimeError("CDP no inicializado. Llama a init_cdp() al arrancar la app.")
    return _cdp


async def init_cdp(postgres_url: str) -> CDPClient:
    global _cdp
    _cdp = CDPClient(postgres_url)
    await _cdp.connect()
    return _cdp


# ── Shorthand conveniente ───────────────────────────────────────────────────
async def track(
    event_type: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    properties: dict | None = None,
) -> str:
    """Shorthand para get_cdp().track(...)"""
    return await get_cdp().track(
        event_type=event_type,
        user_id=user_id,
        agent_id=agent_id,
        properties=properties,
    )
