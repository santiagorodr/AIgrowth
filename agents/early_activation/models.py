"""Modelos de datos del Early Activation Agent."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Channel(str, Enum):
    EMAIL     = "email"
    WHATSAPP  = "whatsapp"
    PUSH      = "push"
    LOG       = "log"       # fallback para POC sin credenciales


class StepKey(str, Enum):
    WELCOME            = "welcome"           # H+0
    CV_TIP             = "cv_tip"            # H+2
    EMPLOYER_SIGNAL    = "employer_signal"   # H+24
    FIRST_APPLY_NUDGE  = "first_apply_nudge" # H+48
    REACTIVATION_CHECK = "reactivation_check"# H+72


class SequenceStepConfig(BaseModel):
    """Definición estática de un paso de la secuencia (no cambia por usuario)."""
    key: StepKey
    delay_hours: int              # Horas después del registro
    channel: Channel
    fallback_channel: Channel = Channel.LOG
    subject: str                  # Asunto del email (o título del push)
    condition: str = "always"     # "always" | "no_application" | "inactive"
    description: str              # Descripción humana del paso


class ActivationEvent(BaseModel):
    """Datos del usuario recién registrado que disparan la secuencia."""
    user_id: str
    full_name: str
    email: str | None = None
    phone: str | None = None       # Formato internacional: +57...
    source: str = "organic"        # Canal de adquisición
    city: str = ""
    current_title: str = ""
    skills: list[str] = Field(default_factory=list)
    experience_years: int = 0
    registered_at: datetime | None = None


class GeneratedMessage(BaseModel):
    """Contenido generado por el LLM para un paso específico."""
    step_key: StepKey
    channel: Channel
    subject: str                   # Para email / push title
    body: str                      # Cuerpo del mensaje (HTML para email, texto para WA)
    whatsapp_text: str = ""        # Versión corta para WhatsApp (si aplica)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelResult(BaseModel):
    """Resultado del intento de envío por un canal."""
    success: bool
    channel: Channel
    message_id: str | None = None
    error: str | None = None
    fallback_used: bool = False


class SequenceStatus(BaseModel):
    """Estado actual de la secuencia de un usuario."""
    user_id: str
    steps_total: int
    steps_sent: int
    steps_pending: int
    steps_failed: int
    next_step: StepKey | None
    next_step_at: datetime | None
    is_complete: bool
