"""
Profile Optimizer — Modelos de datos
"""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class SuggestionPriority(str, Enum):
    HIGH   = "high"    # sin esto el perfil no es competitivo
    MEDIUM = "medium"  # mejora notable con poco esfuerzo
    LOW    = "low"     # detalle fino


class ProfileSuggestion(BaseModel):
    priority:  SuggestionPriority
    field:     str   # "skills" | "title" | "experience" | "education" | "photo" | "summary"
    current:   str   # qué tiene ahora (puede estar vacío)
    suggested: str   # qué debería tener o agregar
    reason:    str   # por qué importa según las vacantes
    effort:    str   # "5 min" | "30 min" | "1-2 días"

    @property
    def priority_emoji(self) -> str:
        return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(self.priority, "⚪")


class OptimizationReport(BaseModel):
    user_id:              str
    full_name:            str
    current_completion:   int                        # % actual
    projected_completion: int                        # % estimado tras aplicar sugerencias
    suggestions:          list[ProfileSuggestion]
    top_job_matches:      list[str] = Field(default_factory=list)  # títulos de vacantes usadas
    summary:              str = ""                  # resumen en 1 línea

    @property
    def improvement(self) -> int:
        return max(0, self.projected_completion - self.current_completion)

    @property
    def high_priority_count(self) -> int:
        return sum(1 for s in self.suggestions if s.priority == SuggestionPriority.HIGH)


class BatchOptimizationResult(BaseModel):
    total_analyzed:  int
    total_sent:      int
    total_skipped:   int
    results:         list[OptimizationReport] = Field(default_factory=list)
    cost_usd:        float = 0.0
    duration_seconds: float = 0.0

    @classmethod
    def empty(cls) -> "BatchOptimizationResult":
        return cls(total_analyzed=0, total_sent=0, total_skipped=0)


class AnalyzeBatchRequest(BaseModel):
    max_completion: int = Field(default=70, ge=1, le=100)


class AnalyzeUserRequest(BaseModel):
    user_id: str
