"""
Churn Predictor — Modelos de datos
────────────────────────────────────
Define los tipos de entrada y salida del agente.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    HIGH   = "high"    # >21 días inactivo o señales fuertes → acción inmediata
    MEDIUM = "medium"  # 14-21 días inactivo → seguimiento
    LOW    = "low"     # 7-14 días → monitorear


class ChurnAnalysis(BaseModel):
    """Resultado del análisis de riesgo de un usuario."""

    user_id:            str
    full_name:          str
    email:              str | None = None
    risk_level:         RiskLevel
    risk_score:         float              # 0.0 – 1.0
    risk_reason:        str                # explicación en español, 1 línea
    key_signals:        list[str]          # 2-4 señales observadas
    recommended_action: str                # "send_reactivation" | "monitor" | "no_action"
    days_inactive:      int
    last_active_at:     datetime
    analyzed_at:        datetime = Field(default_factory=lambda: datetime.utcnow())

    @property
    def risk_emoji(self) -> str:
        return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(self.risk_level, "⚪")

    @property
    def risk_color(self) -> str:
        return {"high": "red", "medium": "yellow", "low": "green"}.get(self.risk_level, "white")


class BatchResult(BaseModel):
    """Resultado del análisis de un lote de usuarios inactivos."""

    total_analyzed:  int
    high_risk:       int
    medium_risk:     int
    low_risk:        int
    analyses:        list[ChurnAnalysis]
    cost_usd:        float = 0.0
    duration_seconds: float = 0.0

    @classmethod
    def from_analyses(cls, analyses: list[ChurnAnalysis]) -> "BatchResult":
        return cls(
            total_analyzed=len(analyses),
            high_risk=sum(1 for a in analyses if a.risk_level == RiskLevel.HIGH),
            medium_risk=sum(1 for a in analyses if a.risk_level == RiskLevel.MEDIUM),
            low_risk=sum(1 for a in analyses if a.risk_level == RiskLevel.LOW),
            analyses=analyses,
        )


class AnalyzeRequest(BaseModel):
    """Request para analizar un usuario específico."""
    user_id: str


class AnalyzeBatchRequest(BaseModel):
    """Request para análisis batch."""
    days_inactive: int = Field(default=7, ge=1, le=365)
