"""
Matching Notifier — Prompts
────────────────────────────
Sistema para Haiku: genera notificaciones cortas y personalizadas.
El objetivo es que el candidato quiera hacer clic — no un email largo.
"""

from __future__ import annotations

MATCHING_SYSTEM = """Eres el asistente de oportunidades de elempleo.com, el principal portal de empleo de Colombia.

Tu tarea es redactar una notificación CORTA y personalizada para alertar a un candidato sobre una vacante que hace match con su perfil.

REGLAS:
- Máximo 3 líneas (40-60 palabras en total)
- Menciona el nombre del candidato, el cargo de la vacante y la empresa
- Incluye el porcentaje de compatibilidad de forma natural
- Termina con una llamada a la acción directa
- Tono: entusiasta pero profesional, en español colombiano (tutéalo)
- NO uses saludos largos, NO repitas información

FORMATO: Texto plano, sin JSON, sin markdown. Solo las 3 líneas del mensaje."""


def matching_prompt(user: dict, job: dict, score: float) -> str:
    """Construye el user_message para Haiku."""
    score_pct  = int(score * 100)
    skills_job = ", ".join((job.get("skills_required") or [])[:4])
    skills_usr = ", ".join((user.get("skills") or [])[:4])

    return f"""VACANTE NUEVA:
- Cargo: {job.get("title", "N/A")}
- Empresa: {job.get("company", "N/A")}
- Ciudad: {job.get("city", "N/A")}
- Modalidad: {job.get("modality", "N/A")}
- Habilidades requeridas: {skills_job or "No especificadas"}

CANDIDATO:
- Nombre: {user.get("full_name", "N/A")}
- Cargo actual: {user.get("current_title", "No especificado")}
- Ciudad: {user.get("city", "N/A")}
- Habilidades: {skills_usr or "No especificadas"}
- Compatibilidad calculada: {score_pct}%

Redacta la notificación corta (máx 3 líneas) para este candidato."""
