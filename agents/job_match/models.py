"""Modelos de datos del Job Match Agent."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """Perfil del usuario para el matching."""
    id: str
    full_name: str = ""
    current_title: str = ""
    city: str = ""
    experience_years: int = 0
    education_level: str = ""
    skills: list[str] = Field(default_factory=list)
    desired_salary: int | None = None
    current_company: str | None = None


class MatchedJob(BaseModel):
    """Una vacante rankeada con su score y explicación."""
    job_id: str
    title: str
    company: str
    city: str
    modality: str = ""
    contract_type: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    category: str = ""
    skills_required: list[str] = Field(default_factory=list)

    # Campos generados por el LLM
    match_score: float = Field(ge=0.0, le=1.0, description="Score 0-1 de compatibilidad")
    match_reason: str = Field(description="Explicación en español de por qué encaja")
    highlights: list[str] = Field(description="2-3 puntos clave del match (badges)")

    # Score semántico crudo de Qdrant (pre-reranking)
    semantic_score: float = 0.0


class JobMatchRequest(BaseModel):
    """Request al agente: perfil del usuario + parámetros opcionales."""
    user: UserProfile
    top_k: int = Field(default=10, ge=1, le=20, description="Número de resultados a retornar")
    filter_city: bool = Field(default=True, description="Filtrar por ciudad del usuario")
    include_remote: bool = Field(default=True, description="Incluir vacantes remotas aunque no sean de la ciudad")


class JobMatchResult(BaseModel):
    """Resultado completo del agente para un usuario."""
    user_id: str
    user_name: str
    jobs: list[MatchedJob]
    total_candidates_evaluated: int = Field(description="Cuántas vacantes evaluó el agente antes del reranking")
    agent_summary: str = Field(description="Resumen en español del perfil del mercado laboral para el usuario")
