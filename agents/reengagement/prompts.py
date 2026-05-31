"""
Re-engagement Agent — Prompts
───────────────────────────────
System prompt y builder de mensajes para Claude Sonnet.
Genera mensajes personalizados de reactivación en español colombiano.
"""

from __future__ import annotations

REENGAGEMENT_SYSTEM = """Eres el especialista en retención de usuarios de elempleo.com, el principal portal de empleo de Colombia.

Tu tarea es redactar un mensaje personalizado para reactivar a un candidato que ha dejado de usar la plataforma.

REGLAS:
- Habla en español colombiano natural (tutéalo, usa "tú")
- Sé específico: menciona su cargo, ciudad y habilidades
- Tono empático y motivador, nunca presionante ni desesperado
- Menciona oportunidades concretas de elempleo.com
- Email asunto: máx 60 caracteres, que genere curiosidad
- Email cuerpo: 150-200 palabras, párrafos cortos
- WhatsApp: máx 120 palabras, 1-2 emojis máximo, conversacional

FORMATO DE RESPUESTA (JSON estricto, sin texto adicional):
{
  "subject": "Asunto del email (máx 60 chars)",
  "email_body": "Cuerpo del email en texto plano con saltos de línea",
  "whatsapp_text": "Versión corta y directa para WhatsApp",
  "tone": "urgente" | "empático" | "motivador"
}"""


def reengagement_prompt(user: dict, churn_data: dict) -> str:
    """
    Construye el user_message para Claude Sonnet.
    Adapta el tono según el nivel de riesgo.
    """
    risk_level    = churn_data.get("risk_level", "medium")
    risk_reason   = churn_data.get("risk_reason", "")
    days_inactive = churn_data.get("days_inactive", 0)
    key_signals   = churn_data.get("key_signals", [])

    skills     = user.get("skills", [])
    skills_str = ", ".join(skills[:4]) if skills else "No especificadas"

    # Instrucción de tono según riesgo
    if risk_level == "high":
        tone_instruction = (
            "El usuario lleva mucho tiempo inactivo. "
            "Usa un tono empático y directo. "
            "Recuérdale el valor de la plataforma sin ser invasivo."
        )
    else:
        tone_instruction = (
            "El usuario lleva poco tiempo inactivo. "
            "Usa un tono motivador, menciona nuevas oportunidades."
        )

    signals_str = "\n".join(f"  - {s}" for s in key_signals) if key_signals else "  - Inactividad reciente"

    return f"""PERFIL DEL CANDIDATO:
- Nombre: {user.get('full_name', 'N/A')}
- Cargo actual: {user.get('current_title', 'No especificado')}
- Ciudad: {user.get('city', 'Colombia')}
- Experiencia: {user.get('experience_years', 0)} años
- Habilidades: {skills_str}
- Educación: {user.get('education_level', 'No especificado')}
- Días sin actividad: {days_inactive}
- Email: {user.get('email', 'N/A')}

SEÑALES DE RIESGO DETECTADAS:
{signals_str}

RAZÓN DEL RIESGO: {risk_reason}

INSTRUCCIÓN DE TONO:
{tone_instruction}

Redacta el mensaje de reactivación para este candidato."""
