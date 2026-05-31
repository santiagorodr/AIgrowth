"""
Employer Signal Agent — Modelos de datos
"""
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field


class EmployerView(BaseModel):
    """Señal de que una empresa vio el perfil de un candidato."""
    user_id:               str
    company_name:          str
    company_category:      str = ""     # "tecnologia" | "finanzas" | etc.
    job_title_viewed:      str = ""     # vacante que motivó la visita
    view_duration_seconds: int = 0      # tiempo que estuvo mirando el perfil
    viewed_at:             datetime


class SignalResult(BaseModel):
    """Resultado de procesar una señal para un candidato."""
    user_id:               str
    full_name:             str
    company_name:          str
    channel:               str
    success:               bool
    message_id:            str | None = None
    notification_preview:  str = ""     # primeras líneas del mensaje enviado


class BatchSignalResult(BaseModel):
    """Resultado del procesamiento de un batch de señales."""
    total_processed: int
    sent_ok:         int
    skipped:         int          # ya notificados en 24h
    results:         list[SignalResult] = Field(default_factory=list)
    cost_usd:        float = 0.0

    @classmethod
    def empty(cls) -> "BatchSignalResult":
        return cls(total_processed=0, sent_ok=0, skipped=0)


class SimulateRequest(BaseModel):
    n: int = Field(default=5, ge=1, le=20)


class ProcessRequest(BaseModel):
    window_minutes: int = Field(default=15, ge=1, le=60)
