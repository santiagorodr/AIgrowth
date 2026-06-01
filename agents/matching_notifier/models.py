"""
Matching Notifier — Modelos de datos
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MatchedUser(BaseModel):
    """Candidato que hace match con una vacante nueva."""
    user_id:           str
    full_name:         str
    email:             str | None = None
    city:              str = ""
    current_title:     str = ""
    match_score:       float         # 0.0-1.0 (similitud coseno Qdrant)
    notification_sent: bool = False
    message_id:        str | None = None


class JobNotificationResult(BaseModel):
    """Resultado del procesamiento de una vacante."""
    job_id:           str
    job_title:        str
    company:          str
    city:             str
    candidates_found: int   # total con score >= threshold
    notified:         int   # enviados exitosamente
    skipped:          int   # ya notificados recientemente
    matched_users:    list[MatchedUser] = Field(default_factory=list)


class BatchNotificationResult(BaseModel):
    """Resultado del batch completo."""
    jobs_processed:  int
    total_notified:  int
    total_skipped:   int
    results:         list[JobNotificationResult] = Field(default_factory=list)
    cost_usd:        float = 0.0
    duration_seconds: float = 0.0

    @classmethod
    def empty(cls) -> "BatchNotificationResult":
        return cls(jobs_processed=0, total_notified=0, total_skipped=0)


class ProcessJobsRequest(BaseModel):
    hours: int = Field(default=6, ge=1, le=168)    # default 6h, hasta 7 días


class ProcessJobRequest(BaseModel):
    job_id: str
