"""
Matching Notifier Agent
──────────────────────────────────────────────────────────────────────────
Flujo inverso al Job Match Agent: cuando aparece una vacante nueva,
busca proactivamente los candidatos que hacen match y los notifica.

Flujo por vacante:
  1. Detecta vacantes publicadas en las últimas N horas (no procesadas aún)
  2. Embebe el texto de la vacante y busca candidatos similares en Qdrant
  3. Filtra por score >= 0.45 y deduplicación (no notificar al mismo usuario
     por la misma vacante en los últimos 7 días)
  4. Genera notificación corta y personalizada con Haiku
  5. Envía por canal + trackea en CDP

Uso:
    agent = MatchingNotifierAgent(cdp=cdp, pool=pool)
    result = await agent.process_new_jobs(hours=24)
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import structlog

from agents.base import BaseAgent
from agents.early_activation.channels import get_channel
from agents.early_activation.models import Channel
from agents.matching_notifier.models import (
    BatchNotificationResult,
    JobNotificationResult,
    MatchedUser,
)
from agents.matching_notifier.prompts import MATCHING_SYSTEM, matching_prompt
from cdp.events import Events
from vector_db.embedder import get_embedder

log = structlog.get_logger(__name__)

MATCH_THRESHOLD   = 0.45   # Score mínimo de similitud coseno
MAX_USERS_PER_JOB = 20     # Máx candidatos a notificar por vacante

# Parámetros configurables via .env
JOB_WINDOW_HOURS  = int(os.getenv("MATCHING_JOB_WINDOW_HOURS", "6"))    # vacantes nuevas
DEDUP_HOURS       = int(os.getenv("MATCHING_DEDUP_HOURS", "72"))         # ventana antiduplicados


class MatchingNotifierAgent(BaseAgent):
    """
    Agente que notifica candidatos cuando aparece una vacante de alto match.

    Parámetros:
        cdp:  CDPClient (opcional)
        bus:  EventBus stub (no usado en Fase 2)
        pool: asyncpg.Pool para queries directas a jobs/users/events
    """

    AGENT_ID = "matching_notifier"

    def __init__(self, cdp=None, bus=None, pool=None):
        super().__init__(cdp=cdp, bus=bus)
        self._pool = pool

    # ── Procesamiento batch ────────────────────────────────────────────────────

    async def process_new_jobs(self, hours: int = JOB_WINDOW_HOURS) -> BatchNotificationResult:
        """
        Procesa todas las vacantes nuevas publicadas en las últimas N horas
        que aún no han sido procesadas.
        """
        if not self._pool:
            self.log.warning("matching.no_pool")
            return BatchNotificationResult.empty()

        new_jobs = await self._get_new_jobs(hours=hours)

        if not new_jobs:
            self.log.info("matching.no_new_jobs", hours=hours)
            return BatchNotificationResult.empty()

        self.log.info("matching.batch_start", jobs=len(new_jobs), hours=hours)
        start   = time.time()
        results: list[JobNotificationResult] = []

        for job in new_jobs:
            try:
                result = await self.process_job(job)
                results.append(result)

                # Marcar la vacante como procesada en el CDP
                await self.track(
                    event_type="matching.job_processed",
                    properties={"job_id": str(job.get("id")), "notified": result.notified},
                )
            except Exception as exc:
                self.log.error("matching.job_error", job_id=str(job.get("id")), error=str(exc))

        batch = BatchNotificationResult(
            jobs_processed=len(results),
            total_notified=sum(r.notified for r in results),
            total_skipped=sum(r.skipped for r in results),
            results=results,
            duration_seconds=round(time.time() - start, 2),
        )
        self.log.info(
            "matching.batch_done",
            jobs=batch.jobs_processed,
            notified=batch.total_notified,
            skipped=batch.total_skipped,
        )
        return batch

    # ── Procesamiento de una vacante ───────────────────────────────────────────

    async def process_job(self, job: dict) -> JobNotificationResult:
        """Procesa una vacante: encuentra candidatos y los notifica."""
        job_id    = str(job.get("id", ""))
        job_title = job.get("title", "")
        company   = job.get("company", "")
        city      = job.get("city", "")

        await self.log_run("started", data={"job_id": job_id})

        # 1. Búsqueda inversa en Qdrant
        matched = await self._find_matching_users(job)

        notified = 0
        skipped  = 0
        result_users: list[MatchedUser] = []

        for candidate in matched[:MAX_USERS_PER_JOB]:
            user_id = candidate.get("user_id", "")
            if not user_id:
                continue

            # 2. Verificar deduplicación
            if await self._is_already_notified(user_id, job_id):
                skipped += 1
                result_users.append(MatchedUser(
                    user_id=user_id,
                    full_name=candidate.get("full_name", ""),
                    city=candidate.get("city", ""),
                    current_title=candidate.get("current_title", ""),
                    match_score=candidate["relevance_score"],
                    notification_sent=False,
                ))
                continue

            # 3. Perfil completo del usuario
            user = await self._get_user_profile(user_id)
            if not user:
                continue

            # 4. Generar notificación con Haiku
            notification_text = await self._generate_notification(
                user=user,
                job=job,
                score=candidate["relevance_score"],
            )

            # 5. Enviar
            success, message_id = await self._send_notification(user, job, notification_text)

            # 6. Trackear en CDP
            await self.track(
                event_type=Events.MATCH_NOTIFICATION_SENT,
                user_id=user_id,
                properties={
                    "job_id":     job_id,
                    "job_title":  job_title,
                    "company":    company,
                    "match_score": candidate["relevance_score"],
                    "message_id": message_id,
                    "success":    success,
                },
            )

            if success:
                notified += 1

            result_users.append(MatchedUser(
                user_id=user_id,
                full_name=user.get("full_name", ""),
                email=user.get("email"),
                city=user.get("city", ""),
                current_title=user.get("current_title", ""),
                match_score=candidate["relevance_score"],
                notification_sent=success,
                message_id=message_id,
            ))

        await self.log_run("completed", data={"job_id": job_id, "notified": notified})

        return JobNotificationResult(
            job_id=job_id,
            job_title=job_title,
            company=company,
            city=city,
            candidates_found=len(matched),
            notified=notified,
            skipped=skipped,
            matched_users=result_users,
        )

    # ── Privados ───────────────────────────────────────────────────────────────

    async def _get_new_jobs(self, hours: int) -> list[dict]:
        """Vacantes nuevas no procesadas aún."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, company, city, category, modality, contract_type,
                       salary_min, salary_max, experience_years, education_level,
                       skills_required, description, requirements, published_at
                FROM jobs
                WHERE is_active = TRUE
                  AND published_at > NOW() - INTERVAL '1 hour' * $1
                  AND NOT EXISTS (
                    SELECT 1 FROM events
                    WHERE event_type = 'matching.job_processed'
                      AND properties->>'job_id' = jobs.id::text
                  )
                ORDER BY published_at DESC
                LIMIT 20
                """,
                hours,
            )
        return [dict(r) for r in rows]

    async def _find_matching_users(self, job: dict) -> list[dict]:
        """Búsqueda inversa en Qdrant: vacante → candidatos."""
        try:
            embedder  = get_embedder()
            job_text  = embedder._job_to_text(job)
            # Buscar en la ciudad de la vacante + sin filtro geográfico si hay pocos resultados
            results   = embedder.search_users(
                query=job_text,
                top_k=MAX_USERS_PER_JOB * 2,
                city=job.get("city"),
                score_threshold=MATCH_THRESHOLD,
            )
            if len(results) < 3:
                # Ampliar búsqueda sin filtro de ciudad
                results = embedder.search_users(
                    query=job_text,
                    top_k=MAX_USERS_PER_JOB * 2,
                    score_threshold=MATCH_THRESHOLD,
                )
            return results
        except Exception as exc:
            self.log.error("matching.search_error", error=str(exc))
            return []

    async def _is_already_notified(self, user_id: str, job_id: str) -> bool:
        """Verifica si ya se notificó a este usuario sobre esta vacante en la ventana de dedup."""
        if not self._pool:
            return False
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                f"""
                SELECT EXISTS (
                    SELECT 1 FROM events
                    WHERE user_id = $1::uuid
                      AND event_type = 'match.notification_sent'
                      AND properties->>'job_id' = $2
                      AND timestamp > NOW() - INTERVAL '1 hour' * {DEDUP_HOURS}
                )
                """,
                user_id,
                job_id,
            )
        return bool(result)

    async def _get_user_profile(self, user_id: str) -> dict | None:
        """Obtiene el perfil completo del usuario desde PostgreSQL."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE id = $1::uuid", user_id
            )
        return dict(row) if row else None

    async def _generate_notification(
        self, user: dict, job: dict, score: float
    ) -> str:
        """Genera notificación corta con Haiku."""
        return await self.llm(
            task_type="classification",   # Haiku
            system=MATCHING_SYSTEM,
            user_message=matching_prompt(user, job, score),
            max_tokens=150,
            temperature=0.4,
        )

    async def _send_notification(
        self, user: dict, job: dict, text: str
    ) -> tuple[bool, str | None]:
        """Envía la notificación por canal (LogChannel en POC)."""
        channel = get_channel(Channel.EMAIL)
        result  = await channel.send(
            to=user.get("email", ""),
            subject=f"Nueva vacante: {job.get('title', '')} en {job.get('company', '')}",
            body=text,
        )
        return result.success, result.message_id
