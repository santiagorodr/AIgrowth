"""
Profile Optimizer — Prompts
────────────────────────────
Sonnet analiza brechas entre el perfil del candidato y lo que
piden las vacantes más relevantes para él.
"""
from __future__ import annotations

OPTIMIZER_SYSTEM = """Eres el consultor de empleabilidad de elempleo.com, especializado en el mercado laboral colombiano.

Tu tarea es analizar el perfil de un candidato comparándolo con las vacantes más relevantes para él, e identificar las mejoras concretas que más impacto tendrían en su búsqueda de empleo.

RESPONDE ÚNICAMENTE con JSON válido, sin texto adicional:
{
  "projected_completion": <número 0-100, completitud estimada tras aplicar sugerencias>,
  "summary": "<1 oración motivadora en español colombiano, específica al candidato>",
  "suggestions": [
    {
      "priority": "high" | "medium" | "low",
      "field": "skills" | "title" | "experience" | "education" | "photo" | "summary",
      "current": "<qué tiene ahora, puede ser vacío si no tiene nada>",
      "suggested": "<qué debería agregar o cambiar, específico>",
      "reason": "<por qué esto importa, citando las vacantes analizadas>",
      "effort": "<tiempo estimado: '5 min', '30 min', '1-2 días'>"}
  ]
}

CRITERIOS DE PRIORIDAD:
- HIGH: habilidades o información que aparecen en >60% de las vacantes relevantes
- MEDIUM: mejoras que aumentarían visibilidad o credibilidad notablemente
- LOW: detalles finos que refinan el perfil

Máximo 5 sugerencias. Ordénalas de mayor a menor prioridad.
Sé específico: menciona tecnologías, cargos o sectores reales del mercado colombiano."""


def optimizer_prompt(user: dict, jobs: list[dict]) -> str:
    """
    Construye el user_message para Sonnet.
    Incluye perfil del usuario + requisitos de las top 5 vacantes.
    """
    skills_usr = ", ".join((user.get("skills") or [])[:8]) or "No especificadas"

    # Formatear vacantes de referencia
    jobs_text = ""
    for i, job in enumerate(jobs[:5], 1):
        skills_req = ", ".join((job.get("skills_required") or [])[:6])
        jobs_text += (
            f"\n{i}. {job.get('title', 'N/A')} @ {job.get('company', 'N/A')} "
            f"({job.get('city', 'N/A')}, {job.get('modality', 'N/A')})\n"
            f"   Skills requeridas: {skills_req or 'No especificadas'}\n"
            f"   Experiencia: {job.get('experience_years', 0)}+ años | "
            f"Educación: {job.get('education_level', 'N/A')}\n"
            f"   Match score: {job.get('relevance_score', 0):.0%}\n"
        )

    return f"""PERFIL ACTUAL DEL CANDIDATO:
- Nombre: {user.get('full_name', 'N/A')}
- Cargo actual: {user.get('current_title', 'No especificado')}
- Ciudad: {user.get('city', 'N/A')}
- Experiencia: {user.get('experience_years', 0)} años
- Educación: {user.get('education_level', 'No especificado')}
- Habilidades declaradas: {skills_usr}
- Completitud del perfil: {user.get('profile_completion', 0)}%
- Salario deseado: ${user.get('desired_salary', 0):,} COP

VACANTES MÁS RELEVANTES PARA ESTE CANDIDATO:
{jobs_text if jobs_text else "No se encontraron vacantes relevantes en este momento."}

Analiza las brechas entre el perfil del candidato y lo que piden las vacantes,
y genera sugerencias concretas y priorizadas para mejorar su perfil."""
