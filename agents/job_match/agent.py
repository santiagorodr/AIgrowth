"""
Job Match Personalization Agent — #6
──────────────────────────────────────
Recomienda las vacantes más relevantes para cada usuario combinando:
  1. Búsqueda semántica en Qdrant (velocidad, escala)
  2. Reranking con Claude Sonnet (comprensión profunda del match)

Flujo completo:
  run(request)
    ├── Búsqueda semántica → top 20 candidatos (Qdrant, <50ms)
    ├── Reranking LLM      → top K con score + explicación (Claude, ~2s)
    ├── Track evento       → CDP + Event Bus
    └── Retorna JobMatchResult

Uso:
    agent = JobMatchAgent()
    result = await agent.run(JobMatchRequest(
        user=UserProfile(id="user-001", ...),
        top_k=10
    ))
"""

from __future__ import annotations

import json
import re

import structlog

from agents.base import BaseAgent
from cdp.events import Events
from event_bus.bus import Channels
from vector_db.embedder import get_embedder

from .models import JobMatchRequest, JobMatchResult, MatchedJob, UserProfile
from .prompts import RERANKING_SYSTEM, reranking_user

log = structlog.get_logger("job_match_agent")


class JobMatchAgent(BaseAgent):
    """
    Agente de personalización de vacantes.

    Combina búsqueda vectorial semántica con reranking inteligente
    por LLM para entregar recomendaciones altamente relevantes.
    """

    AGENT_ID = "job_match_agent"

    # ── Punto de entrada principal ───────────────────────────────────────────
    async def run(self, request: JobMatchRequest) -> JobMatchResult:
        """
        Ejecuta el pipeline completo de matching para un usuario.

        Pasos:
          1. Búsqueda semántica en Qdrant → top 20 candidatos
          2. Reranking con Claude Sonnet  → top K con scores y explicaciones
          3. Trackear evento en CDP
          4. Publicar en Event Bus (para que otros agentes reaccionen)
        """
        user = request.user
        await self.log_run("started", {"user_id": user.id}, user_id=user.id)

        # ── PASO 1: Búsqueda semántica ───────────────────────────────────────
        log.info("job_match.searching", user_id=user.id, city=user.city)

        embedder = get_embedder()

        # Buscar por perfil completo (título + skills + ciudad)
        candidates = embedder.recommend_for_user(
            user=user.model_dump(),
            top_k=20,
            city=user.city if request.filter_city else None,
        )

        # Si filtramos por ciudad y hay pocos resultados, ampliar búsqueda
        if request.filter_city and len(candidates) < 5:
            log.info("job_match.expanding_search", reason="pocos_resultados_en_ciudad")
            candidates = embedder.recommend_for_user(
                user=user.model_dump(),
                top_k=20,
                city=None,  # Sin filtro de ciudad
            )

        # Si se incluyen remotos y se filtró por ciudad, mezclar con remotas
        if request.filter_city and request.include_remote:
            remote_candidates = embedder.search_jobs(
                query=self._build_query(user),
                top_k=10,
                modality="remoto",
            )
            # Agregar remotas que no estén ya en la lista
            existing_ids = {c["job_id"] for c in candidates}
            for rc in remote_candidates:
                if rc["job_id"] not in existing_ids:
                    candidates.append(rc)

        total_candidates = len(candidates)
        log.info("job_match.candidates_found", count=total_candidates, user_id=user.id)

        if not candidates:
            return JobMatchResult(
                user_id=user.id,
                user_name=user.full_name,
                jobs=[],
                total_candidates_evaluated=0,
                agent_summary="No encontramos vacantes activas que coincidan con tu perfil en este momento. ¡Vuelve pronto, actualizamos vacantes todos los días!",
            )

        # ── PASO 2: Reranking con LLM ────────────────────────────────────────
        log.info("job_match.reranking", user_id=user.id, candidates=total_candidates)

        llm_response = await self.llm(
            task_type="reasoning",
            system=RERANKING_SYSTEM,
            user_message=reranking_user(
                user=user.model_dump(),
                candidates=candidates,
                top_k=request.top_k,
            ),
            max_tokens=3000,
            temperature=0.2,  # Bajo: queremos consistencia, no creatividad extrema
        )

        # ── PASO 3: Parsear respuesta JSON del LLM ───────────────────────────
        ranked_jobs, agent_summary = self._parse_llm_response(llm_response, candidates)

        log.info(
            "job_match.reranked",
            user_id=user.id,
            returned=len(ranked_jobs),
            top_score=ranked_jobs[0].match_score if ranked_jobs else 0,
        )

        # ── PASO 4: Trackear en CDP ──────────────────────────────────────────
        await self.track(
            event_type=Events.AGENT_COMPLETED,
            user_id=user.id,
            properties={
                "candidates_evaluated": total_candidates,
                "jobs_returned": len(ranked_jobs),
                "top_match_score": ranked_jobs[0].match_score if ranked_jobs else 0,
                "top_job_id": ranked_jobs[0].job_id if ranked_jobs else None,
            },
        )

        # ── PASO 5: Publicar en Event Bus ────────────────────────────────────
        if ranked_jobs:
            await self.publish(
                channel=Channels.CONVERSIONS,
                event="job_match.recommendations_ready",
                data={
                    "user_id": user.id,
                    "top_jobs": [j.job_id for j in ranked_jobs[:3]],
                    "top_score": ranked_jobs[0].match_score,
                },
            )

        await self.log_run("completed", {"jobs_returned": len(ranked_jobs)}, user_id=user.id)

        return JobMatchResult(
            user_id=user.id,
            user_name=user.full_name,
            jobs=ranked_jobs,
            total_candidates_evaluated=total_candidates,
            agent_summary=agent_summary,
        )

    # ── Búsqueda directa por texto (sin perfil) ──────────────────────────────
    async def search(
        self,
        query: str,
        city: str | None = None,
        top_k: int = 10,
    ) -> list[dict]:
        """
        Búsqueda semántica directa por texto libre.
        Útil para el buscador del WhatsApp Agent.
        """
        embedder = get_embedder()
        return embedder.search_jobs(query=query, city=city, top_k=top_k)

    # ── Helpers privados ─────────────────────────────────────────────────────
    def _build_query(self, user: UserProfile) -> str:
        """Construye la query de búsqueda semántica desde el perfil."""
        parts = [
            user.current_title,
            " ".join(user.skills[:5]),
            user.education_level,
            f"{user.experience_years} años experiencia",
        ]
        return " ".join(p for p in parts if p)

    def _parse_llm_response(
        self,
        llm_response: str,
        candidates: list[dict],
    ) -> tuple[list[MatchedJob], str]:
        """
        Parsea la respuesta JSON del LLM y la cruza con los datos reales
        de las vacantes candidatas para construir los MatchedJob completos.
        """
        # Índice de candidatos para lookup rápido
        candidates_by_id = {c["job_id"]: c for c in candidates}

        # Extraer JSON (el LLM a veces añade markdown ```json...```)
        json_str = self._extract_json(llm_response)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            log.error("job_match.json_parse_error", error=str(e), raw=llm_response[:200])
            # Fallback: retornar los candidatos sin reranking
            return self._fallback_result(candidates), "Encontramos oportunidades interesantes para tu perfil."

        ranked_jobs: list[MatchedJob] = []
        for item in data.get("ranked_jobs", []):
            job_id = item.get("job_id")
            candidate = candidates_by_id.get(job_id)
            if not candidate:
                continue  # El LLM inventó un ID — ignorar

            ranked_jobs.append(
                MatchedJob(
                    job_id=job_id,
                    title=candidate.get("title", ""),
                    company=candidate.get("company", ""),
                    city=candidate.get("city", ""),
                    modality=candidate.get("modality", ""),
                    contract_type=candidate.get("contract_type", ""),
                    salary_min=candidate.get("salary_min"),
                    salary_max=candidate.get("salary_max"),
                    category=candidate.get("category", ""),
                    skills_required=candidate.get("skills_required", []),
                    match_score=float(item.get("match_score", 0.5)),
                    match_reason=item.get("match_reason", ""),
                    highlights=item.get("highlights", [])[:3],
                    semantic_score=candidate.get("relevance_score", 0.0),
                )
            )

        agent_summary = data.get("agent_summary", "¡Hay buenas oportunidades esperándote!")
        return ranked_jobs, agent_summary

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extrae el JSON de la respuesta del LLM, removiendo markdown si hay."""
        # Caso 1: viene dentro de ```json ... ```
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            return match.group(1).strip()
        # Caso 2: viene limpio (el caso ideal)
        text = text.strip()
        if text.startswith("{"):
            return text
        # Caso 3: buscar el primer { hasta el último }
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return text[start:end]
        return text

    @staticmethod
    def _fallback_result(candidates: list[dict]) -> list[MatchedJob]:
        """Fallback cuando el LLM falla: retorna los top candidatos por score semántico."""
        jobs = []
        for c in sorted(candidates, key=lambda x: x.get("relevance_score", 0), reverse=True)[:5]:
            jobs.append(
                MatchedJob(
                    job_id=c["job_id"],
                    title=c.get("title", ""),
                    company=c.get("company", ""),
                    city=c.get("city", ""),
                    modality=c.get("modality", ""),
                    contract_type=c.get("contract_type", ""),
                    salary_min=c.get("salary_min"),
                    salary_max=c.get("salary_max"),
                    category=c.get("category", ""),
                    skills_required=c.get("skills_required", []),
                    match_score=c.get("relevance_score", 0.5),
                    match_reason="Vacante relevante para tu perfil.",
                    highlights=["Resultado relevante"],
                    semantic_score=c.get("relevance_score", 0.0),
                )
            )
        return jobs
