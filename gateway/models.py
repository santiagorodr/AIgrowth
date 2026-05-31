"""Modelos Pydantic para el LLM Gateway."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """
    Tipo de tarea → determina qué modelo usar.
    - GENERATION / REASONING  → Sonnet  (más capaz, más caro)
    - CLASSIFICATION / EXTRACTION → Haiku (más rápido, más barato)
    """
    GENERATION     = "generation"      # Crear contenido SEO, mensajes WA, emails
    REASONING      = "reasoning"       # Analizar trends, diseñar estrategias
    CLASSIFICATION = "classification"  # Categorizar vacantes, detectar intención
    EXTRACTION     = "extraction"      # Extraer datos de texto, parsear CVs
    CONVERSATION   = "conversation"    # Chat conversacional (WhatsApp, Career Copilot)


class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class CompletionRequest(BaseModel):
    agent_id: str = Field(description="ID del agente que hace la llamada, ej: 'job_match_agent'")
    task_type: TaskType = Field(default=TaskType.GENERATION)
    system: str | None = Field(default=None, description="System prompt")
    messages: list[Message] = Field(description="Historial de mensajes")
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageStats(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    model_used: str
    latency_ms: int


class CompletionResponse(BaseModel):
    content: str
    usage: UsageStats
    agent_id: str
    task_type: str


class HealthResponse(BaseModel):
    status: str
    gateway: str = "ok"
    anthropic_api: str
    total_calls_today: int
    total_cost_today_usd: float
