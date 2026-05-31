"""
Job Match Agent — FastAPI Router
──────────────────────────────────
Expone el agente como servicio HTTP.

Endpoints:
  POST /agents/job-match/recommend
      Recibe un perfil completo, retorna vacantes rankeadas con explicaciones

  GET  /agents/job-match/recommend/{user_id}
      Carga el perfil desde la DB y retorna recomendaciones

  POST /agents/job-match/search
      Búsqueda semántica libre por texto (sin perfil de usuario)
"""

from __future__ import annotations

import json
import os

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .agent import JobMatchAgent
from .models import JobMatchRequest, JobMatchResult, UserProfile

router = APIRouter(prefix="/agents/job-match", tags=["Job Match Agent"])

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "",
)

# Instancia global del agente (sin CDP/Bus en modo standalone)
_agent = JobMatchAgent()


# ── POST /recommend ──────────────────────────────────────────────────────────
@router.post("/recommend", response_model=JobMatchResult)
async def recommend(request: JobMatchRequest) -> JobMatchResult:
    """
    Recibe un perfil de usuario y retorna sus vacantes más relevantes,
    rankeadas y con explicaciones personalizadas generadas por IA.

    Ejemplo de body:
    ```json
    {
      "user": {
        "id": "user-001",
        "full_name": "Andrés García",
        "current_title": "Desarrollador Backend Junior",
        "city": "Bogotá",
        "experience_years": 2,
        "skills": ["Python", "Django", "PostgreSQL"],
        "desired_salary": 7000000
      },
      "top_k": 8
    }
    ```
    """
    try:
        return await _agent.run(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /recommend/{user_id} ─────────────────────────────────────────────────
@router.get("/recommend/{user_id}", response_model=JobMatchResult)
async def recommend_by_id(user_id: str, top_k: int = 10) -> JobMatchResult:
    """
    Carga el perfil del usuario desde PostgreSQL y retorna recomendaciones.
    Usa el user_id de la tabla `users` (UUID).
    """
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    try:
        row = await pool.fetchrow(
            "SELECT * FROM users WHERE id = $1", user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"Usuario '{user_id}' no encontrado")

        user = UserProfile(
            id=str(row["id"]),
            full_name=row["full_name"] or "",
            current_title=row["current_title"] or "",
            city=row["city"] or "",
            experience_years=row["experience_years"] or 0,
            education_level=row["education_level"] or "",
            skills=row["skills"] or [],
            desired_salary=row["desired_salary"],
            current_company=row["current_company"],
        )
    finally:
        await pool.close()

    request = JobMatchRequest(user=user, top_k=top_k)
    try:
        return await _agent.run(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /search ─────────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    city: str | None = None
    top_k: int = 10


@router.post("/search")
async def search(request: SearchRequest) -> dict:
    """
    Búsqueda semántica libre. No requiere perfil de usuario.
    Útil para el buscador del WhatsApp Agent.

    Ejemplo: {"query": "desarrollador python remoto con experiencia en APIs"}
    """
    try:
        results = await _agent.search(
            query=request.query,
            city=request.city,
            top_k=request.top_k,
        )
        return {"query": request.query, "results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /health ──────────────────────────────────────────────────────────────
@router.get("/health")
async def health() -> dict:
    return {"agent": "job_match_agent", "status": "ok"}
