"""
Profile Optimizer Agent
──────────────────────────────────────────────────────────────────────────
Analiza la brecha entre el perfil del candidato y las vacantes más
relevantes para él, y genera sugerencias concretas y priorizadas.

Flujo por usuario:
  1. Obtiene las top 5 vacantes más relevantes del candidato desde Qdrant
  2. Envía perfil + requisitos de vacantes a Claude Sonnet
  3. Sonnet identifica gaps y genera sugerencias (priority + effort)
  4. Envía reporte por canal + trackea en CDP

Trigger: usuarios con profile_completion < umbral que no recibieron
         sugerencias en los últimos 7 días.

Uso:
    agent = ProfileOptimizerAgent(cdp=cdp, pool=pool)
    result = await agent.analyze_batch(max_completion=70)
"""
from __future__ import annotations

import json
import time

import structlog

from agents.base import BaseAgent
from agents.early_activation.channels import get_channel
from agents.early_activation.models import Channel
from agents.profile_optimizer.models import (
    BatchOptimizationResult,
    OptimizationReport,
    ProfileSuggestion,
    SuggestionPriority,
)
from agents.profile_optimizer.prompts import OPTIMIZER_SYSTEM, optimizer_prompt
from cdp.events import Events
from vector_db.embedder import get_embedder

log = structlog.get_logger(__name__)


class ProfileOptimizerAgent(BaseAgent):
    """
    Agente que genera sugerencias de mejora de perfil personalizadas.

    Parámetros:
        cdp:  CDPClient (opcional)
        pool: asyncpg.Pool para queries a users/events
    """

    AGENT_ID = "profile_optimizer"

    def __init__(self, cdp=None, bus=None, pool=None):
        super().__init__(cdp=cdp, bus=bus)
        self._pool = pool

    # ── Batch ──────────────────────────────────────────────────────────────────

    async def analyze_batch(self, max_completion: int = 70) -> BatchOptimizationResult:
        """Analiza todos los usuarios con perfil incompleto pendientes."""
        if not self._pool:
            self.log.warning("optimizer.no_pool")
            return BatchOptimizationResult.empty()

        users = await self._get_users_to_optimize(max_completion)
        if not users:
            self.log.info("optimizer.no_users", max_completion=max_completion)
            return BatchOptimizationResult.empty()

        self.log.info("optimizer.batch_start", users=len(users))
        start   = time.time()
        reports = []
        skipped = 0

        for user in users:
            user_id = str(user.get("id", ""))
            try:
                if await self._is_recently_optimized(user_id):
                    skipped += 1
                    continue
                report = await self.analyze_user(user)
                reports.append(report)
            except Exception as exc:
                self.log.error("optimizer.user_error", user_id=user_id, error=str(exc))

        result = BatchOptimizationResult(
            total_analyzed=len(users),
            total_sent=len(reports),
            total_skipped=skipped,
            results=reports,
            duration_seconds=round(time.time() - start, 2),
        )
        self.log.info("optimizer.batch_done",
                      analyzed=result.total_analyzed,
                      sent=result.total_sent,
                      skipped=result.total_skipped)
        return result

    # ── Individual ─────────────────────────────────────────────────────────────

    async def analyze_user(self, user: dict) -> OptimizationReport:
        """Genera el reporte de optimización para un usuario específico."""
        user_id = str(user.get("id", ""))
        await self.log_run("started", user_id=user_id)

        # 1. Vacantes más relevantes para el usuario
        jobs = await self._get_relevant_jobs(user)

        # 2. Análisis con Sonnet
        report = await self._generate_suggestions(user, jobs)

        # 3. Enviar reporte
        success, message_id = await self._send_report(user, report)

        # 4. Trackear en CDP
        await self.track(
            event_type=Events.PROFILE_OPTIMIZATION_SENT,
            user_id=user_id,
            properties={
                "current_completion":   report.current_completion,
                "projected_completion": report.projected_completion,
                "improvement":          report.improvement,
                "suggestions_count":    len(report.suggestions),
                "high_priority":        report.high_priority_count,
                "message_id":           message_id,
            },
        )

        await self.log_run("completed", user_id=user_id)
        return report

    # ── Privados ───────────────────────────────────────────────────────────────

    async def _get_users_to_optimize(self, max_completion: int) -> list[dict]:
        """Usuarios con perfil incompleto sin optimización reciente."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, email, full_name, current_title, city,
                       skills, experience_years, education_level,
                       profile_completion, desired_salary
                FROM users
                WHERE is_active = TRUE
                  AND profile_completion < $1
                  AND NOT EXISTS (
                    SELECT 1 FROM events
                    WHERE user_id = users.id
                      AND event_type = 'profile.optimization_suggested'
                      AND timestamp > NOW() - INTERVAL '7 days'
                  )
                ORDER BY profile_completion ASC
                LIMIT 50
                """,
                max_completion,
            )
        return [dict(r) for r in rows]

    async def _is_recently_optimized(self, user_id: str) -> bool:
        """Verifica si el usuario recibió sugerencias en los últimos 7 días."""
        if not self._pool:
            return False
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM events
                    WHERE user_id = $1::uuid
                      AND event_type = 'profile.optimization_suggested'
                      AND timestamp > NOW() - INTERVAL '7 days'
                )
                """,
                user_id,
            )
        return bool(result)

    async def _get_relevant_jobs(self, user: dict) -> list[dict]:
        """Obtiene las top 5 vacantes más relevantes para el usuario usando Qdrant."""
        try:
            embedder = get_embedder()
            return embedder.recommend_for_user(user=user, top_k=5)
        except Exception as exc:
            self.log.warning("optimizer.jobs_error", error=str(exc))
            return []

    async def _generate_suggestions(
        self, user: dict, jobs: list[dict]
    ) -> OptimizationReport:
        """Llama a Sonnet para generar el análisis de gaps."""
        raw = await self.llm(
            task_type="reasoning",
            system=OPTIMIZER_SYSTEM,
            user_message=optimizer_prompt(user, jobs),
            max_tokens=1200,
            temperature=0.3,
        )
        return self._parse_suggestions(raw, user, jobs)

    def _parse_suggestions(
        self, raw: str, user: dict, jobs: list[dict]
    ) -> OptimizationReport:
        """Parsea el JSON de Sonnet. Genera reporte fallback si falla."""
        current = user.get("profile_completion", 0)
        job_titles = [j.get("title", "") for j in jobs[:5]]

        try:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON encontrado")

            data        = json.loads(raw[start:end])
            suggestions = []

            for s in data.get("suggestions", [])[:5]:
                try:
                    suggestions.append(ProfileSuggestion(
                        priority=SuggestionPriority(s.get("priority", "medium")),
                        field=s.get("field", "skills"),
                        current=s.get("current", ""),
                        suggested=s.get("suggested", ""),
                        reason=s.get("reason", ""),
                        effort=s.get("effort", "30 min"),
                    ))
                except Exception:
                    continue

            return OptimizationReport(
                user_id=str(user.get("id", "")),
                full_name=user.get("full_name", ""),
                current_completion=current,
                projected_completion=min(100, int(data.get("projected_completion", current + 10))),
                suggestions=suggestions,
                top_job_matches=job_titles,
                summary=data.get("summary", ""),
            )

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            self.log.warning("optimizer.parse_error", error=str(exc), raw=raw[:200])

            # Fallback genérico basado en qué campos están vacíos
            fallback_suggestions = []
            skills = user.get("skills") or []
            if len(skills) < 3:
                fallback_suggestions.append(ProfileSuggestion(
                    priority=SuggestionPriority.HIGH,
                    field="skills",
                    current=", ".join(skills) if skills else "Sin habilidades",
                    suggested="Agrega al menos 5 habilidades técnicas relevantes a tu área",
                    reason="Los perfiles con más de 5 habilidades reciben 3x más visitas",
                    effort="5 min",
                ))
            if not user.get("current_title"):
                fallback_suggestions.append(ProfileSuggestion(
                    priority=SuggestionPriority.HIGH,
                    field="title",
                    current="Sin cargo actual",
                    suggested="Agrega tu cargo actual o el cargo al que aspiras",
                    reason="El cargo es el primer dato que ven las empresas",
                    effort="5 min",
                ))

            return OptimizationReport(
                user_id=str(user.get("id", "")),
                full_name=user.get("full_name", ""),
                current_completion=current,
                projected_completion=min(100, current + 15),
                suggestions=fallback_suggestions,
                top_job_matches=job_titles,
                summary="Completa tu perfil para aumentar tus probabilidades de ser contactado.",
            )

    async def _send_report(
        self, user: dict, report: OptimizationReport
    ) -> tuple[bool, str | None]:
        """Envía el reporte por canal (LogChannel en POC)."""
        lines = [
            f"Hola {user.get('full_name', '').split()[0]}, aquí están tus sugerencias de mejora:",
            "",
            f"📊 Completitud actual: {report.current_completion}% → {report.projected_completion}% proyectado",
            f"💡 {report.summary}",
            "",
        ]
        for s in report.suggestions:
            lines.append(f"{s.priority_emoji} [{s.field.upper()}] {s.suggested}")
            lines.append(f"   Por qué: {s.reason}")
            lines.append(f"   Esfuerzo: {s.effort}")
            lines.append("")

        channel = get_channel(Channel.EMAIL)
        result  = await channel.send(
            to=user.get("email", ""),
            subject=f"Mejora tu perfil en elempleo — {report.improvement}% más en 10 min",
            body="\n".join(lines),
        )
        return result.success, result.message_id
