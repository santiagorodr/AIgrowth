"""
Employer Signal Agent — Prompts
────────────────────────────────
Haiku genera notificaciones cortas y motivadoras.
El tono es de "¡alguien te está buscando!" — urgencia positiva.
"""
from __future__ import annotations

EMPLOYER_SIGNAL_SYSTEM = """Eres el asistente de oportunidades de elempleo.com.

Una empresa acaba de revisar el perfil de un candidato. Tu tarea es escribir una notificación CORTA (2-3 líneas máximo) que:
1. Le diga al candidato que una empresa vio su perfil
2. Lo motive a actuar ahora (es el momento)
3. Sea específica: menciona la empresa y el cargo si aplica

REGLAS:
- Máximo 40-50 palabras
- Tono: entusiasta, genuino, no exagerado
- En español colombiano, tutéalo
- Termina con una llamada a la acción corta
- NO uses emojis en exceso (máximo 1)

FORMATO: Texto plano, sin JSON, solo el mensaje."""


def employer_signal_prompt(user: dict, view: dict) -> str:
    """Construye el user_message para Haiku."""
    company       = view.get("company_name", "Una empresa")
    job_title     = view.get("job_title_viewed", "")
    duration      = view.get("view_duration_seconds", 0)
    user_title    = user.get("current_title", "tu perfil")

    duration_hint = ""
    if duration >= 60:
        duration_hint = f" (estuvo {duration // 60} min revisándolo)"
    elif duration >= 30:
        duration_hint = " (revisó tu perfil con detenimiento)"

    job_context = f" para la posición de {job_title}" if job_title else ""

    return f"""DATOS DE LA VISITA:
- Empresa: {company}
- Candidato: {user.get('full_name', 'N/A')}
- Cargo del candidato: {user_title}
- Ciudad: {user.get('city', 'N/A')}
- Duración de la visita: {duration}s{duration_hint}
- Vacante consultada: {job_title or 'No especificada'}

Genera la notificación motivadora para {user.get('full_name', '').split()[0]}, diciéndole que {company} revisó su perfil{job_context}."""
