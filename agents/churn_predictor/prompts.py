"""
Churn Predictor — Prompts
──────────────────────────
System prompt y builder de mensajes para Claude Haiku.
Haiku es suficiente para clasificación — barato y rápido.
"""

from __future__ import annotations

CHURN_SYSTEM = """Eres un analista de retención de usuarios para elempleo.com, el principal portal de empleo de Colombia.

Tu tarea es analizar el perfil e historial de un candidato y clasificar su riesgo de abandono (churn).

Responde ÚNICAMENTE con un JSON válido sin texto adicional, con esta estructura exacta:
{
  "risk_level": "high" | "medium" | "low",
  "risk_score": <número entre 0.0 y 1.0>,
  "risk_reason": "<explicación breve en español, máximo 1 oración>",
  "key_signals": ["<señal 1>", "<señal 2>", "<señal 3>"],
  "recommended_action": "send_reactivation" | "monitor" | "no_action"
}

Criterios de clasificación:
- HIGH (score 0.7-1.0): Sin actividad >21 días, sin postulaciones, perfil incompleto, o señales de frustración
- MEDIUM (score 0.4-0.7): Sin actividad 14-21 días, pocas interacciones, o perfil parcialmente completo
- LOW (score 0.1-0.4): Sin actividad 7-14 días, pero con historial de engagement o perfil completo

Acciones recomendadas:
- send_reactivation: Usuario necesita ser contactado activamente (riesgo alto/medio)
- monitor: Observar por unos días más antes de actuar (riesgo medio/bajo)
- no_action: Usuario probablemente volverá solo (riesgo bajo con buenas señales)"""


def churn_user_message(user: dict, events: list[dict], days_inactive: int) -> str:
    """
    Construye el mensaje para Claude Haiku con el contexto del usuario.
    Mantiene el prompt conciso para minimizar tokens (Haiku es barato pero cada token cuenta).
    """
    # Formatear últimos eventos
    recent_events = []
    for e in events[:10]:  # máximo 10 eventos
        event_type = e.get("event_type", "unknown")
        recent_events.append(f"- {event_type}")

    events_summary = "\n".join(recent_events) if recent_events else "- Sin eventos registrados"

    # Habilidades (máx 5 para no inflar el prompt)
    skills = user.get("skills", [])
    skills_str = ", ".join(skills[:5]) if skills else "No especificadas"

    return f"""PERFIL DEL CANDIDATO:
- Nombre: {user.get('full_name', 'N/A')}
- Ciudad: {user.get('city', 'N/A')}
- Cargo actual: {user.get('current_title', 'No especificado')}
- Experiencia: {user.get('experience_years', 0)} años
- Educación: {user.get('education_level', 'No especificado')}
- Habilidades: {skills_str}
- Completitud del perfil: {user.get('profile_completion', 0)}%
- Días sin actividad: {days_inactive}
- Fuente de registro: {user.get('source', 'organic')}

ÚLTIMOS EVENTOS EN LA PLATAFORMA:
{events_summary}

Analiza este candidato y determina su riesgo de abandono."""
