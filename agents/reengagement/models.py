"""
Re-engagement Agent — Modelos de datos
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReengagementMessage(BaseModel):
    """Mensaje de reactivación generado por Claude Sonnet."""
    subject:       str   # asunto del email (≤60 chars)
    email_body:    str   # cuerpo del email en texto plano
    whatsapp_text: str   # versión corta para WhatsApp (≤160 palabras)
    tone:          str = "empático"  # "urgente" | "empático" | "motivador"


class SendResult(BaseModel):
    """Resultado de envío a un usuario."""
    user_id:         str
    full_name:       str
    risk_level:      str           # "high" | "medium"
    channel:         str           # "email" | "whatsapp" | "log"
    success:         bool
    message_id:      str | None = None
    error:           str | None = None
    subject_preview: str = ""      # primeros 60 chars del asunto


class BatchSendResult(BaseModel):
    """Resultado del procesamiento de un lote de usuarios pendientes."""
    total_processed: int
    sent_ok:         int
    sent_failed:     int
    results:         list[SendResult]
    cost_usd:        float = 0.0
    duration_seconds: float = 0.0

    @classmethod
    def empty(cls) -> "BatchSendResult":
        return cls(total_processed=0, sent_ok=0, sent_failed=0, results=[])


class ProcessRequest(BaseModel):
    """Request para procesar usuarios pendientes."""
    limit: int = Field(default=50, ge=1, le=200)


class ProcessUserRequest(BaseModel):
    """Request para procesar un usuario específico."""
    user_id:    str
    risk_level: str = "high"   # nivel de riesgo del churn detectado
    risk_reason: str = ""
    days_inactive: int = 0
