"""
Definición de la secuencia de 72 horas del Early Activation Agent.

Los 5 pasos están ordenados por delay. Cada paso tiene:
  - delay_hours : cuánto esperar desde el registro
  - channel     : canal principal
  - condition   : cuándo se envía ("always", "no_application", "inactive")
  - subject     : asunto del email o título del push

Esta es la configuración que va a producción. Se puede ajustar sin
tocar la lógica del agente.
"""

from __future__ import annotations

from .models import Channel, SequenceStepConfig, StepKey

# ── Secuencia completa ────────────────────────────────────────────────────────
SEQUENCE: list[SequenceStepConfig] = [

    SequenceStepConfig(
        key=StepKey.WELCOME,
        delay_hours=0,
        channel=Channel.EMAIL,
        fallback_channel=Channel.LOG,
        subject="¡Bienvenido a elempleo! Tus primeras vacantes te esperan 🚀",
        condition="always",
        description=(
            "Primer contacto inmediato al registro. "
            "Entrega valor instantáneo con las 3 mejores vacantes personalizadas "
            "y explica cómo completar el perfil para ver más oportunidades."
        ),
    ),

    SequenceStepConfig(
        key=StepKey.CV_TIP,
        delay_hours=2,
        channel=Channel.WHATSAPP,
        fallback_channel=Channel.EMAIL,
        subject="Un tip rápido para que más empresas te encuentren 💡",
        condition="always",
        description=(
            "2 horas después del registro. "
            "Mensaje corto y conversacional por WhatsApp con un consejo específico "
            "para mejorar el perfil basado en las skills del usuario. "
            "Incluye el % actual de completitud y cuánto sube al agregar ese elemento."
        ),
    ),

    SequenceStepConfig(
        key=StepKey.EMPLOYER_SIGNAL,
        delay_hours=24,
        channel=Channel.EMAIL,
        fallback_channel=Channel.LOG,
        subject="📢 Empresas en {city} están buscando tu perfil ahora",
        condition="always",
        description=(
            "24 horas después. Email con señales reales de demanda: "
            "cuántas empresas en su ciudad publicaron vacantes en su categoría "
            "esta semana. Crea urgencia con datos reales del mercado. "
            "Incluye 5 vacantes nuevas que no ha visto."
        ),
    ),

    SequenceStepConfig(
        key=StepKey.FIRST_APPLY_NUDGE,
        delay_hours=48,
        channel=Channel.WHATSAPP,
        fallback_channel=Channel.EMAIL,
        subject="Todavía no has aplicado — aquí están tus mejores vacantes 🎯",
        condition="no_application",   # Solo si no ha aplicado aún
        description=(
            "48 horas después, SOLO si el usuario no ha aplicado a ninguna vacante. "
            "Mensaje directo y motivador por WhatsApp con las top 5 vacantes. "
            "Tono: cálido pero con sentido de urgencia (vacantes se llenan rápido)."
        ),
    ),

    SequenceStepConfig(
        key=StepKey.REACTIVATION_CHECK,
        delay_hours=72,
        channel=Channel.EMAIL,
        fallback_channel=Channel.LOG,
        subject="¿Encontraste lo que buscabas? Queremos ayudarte más 🤝",
        condition="inactive",         # Solo si sigue inactivo a las 72h
        description=(
            "72 horas después, SOLO si el usuario sigue inactivo (no aplicó, "
            "no completó perfil, no tiene actividad reciente). "
            "Email empático que pregunta si necesita ayuda y ofrece el Career Copilot. "
            "Si no responde en 24h más → pasar al Dormant Reactivation Agent."
        ),
    ),
]

# Índice rápido por key
SEQUENCE_BY_KEY: dict[StepKey, SequenceStepConfig] = {s.key: s for s in SEQUENCE}
