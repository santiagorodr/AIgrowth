"""
Prompts del Job Match Personalization Agent
────────────────────────────────────────────
Dos prompts principales:
  1. RERANKING_SYSTEM  — define el rol y reglas del agente
  2. reranking_user()  — construye el prompt con perfil + candidatos para reranking
  3. summary_user()    — genera el resumen de mercado laboral para el usuario
"""

from __future__ import annotations


# ── System Prompt ─────────────────────────────────────────────────────────────
RERANKING_SYSTEM = """Eres el Job Match Agent de elempleo, el motor de recomendación de empleo más inteligente de Colombia.

Tu función es analizar el perfil de un candidato y una lista de vacantes candidatas, y devolver:
1. Un ranking de las vacantes más relevantes, ordenadas de mayor a menor compatibilidad
2. Para cada vacante: un score numérico, una explicación personalizada y highlights clave
3. Un resumen ejecutivo del panorama laboral para ese perfil

REGLAS CRÍTICAS:
- Responde SIEMPRE en español colombiano natural, cálido y motivador
- El campo "match_reason" debe ser específico y personal: menciona las skills del candidato, su experiencia, su ciudad y su expectativa salarial cuando sean relevantes
- El campo "highlights" son badges cortos (máx 40 chars cada uno), ej: "Python + FastAPI ✓", "Salario: dentro del rango", "Remoto disponible"
- El match_score va de 0.0 a 1.0: 0.9+ = match casi perfecto, 0.7-0.9 = muy buen match, 0.5-0.7 = match parcial
- NO incluyas vacantes con score < 0.40 en el resultado final
- El "agent_summary" debe ser inspirador pero honesto: 2-3 oraciones sobre qué tan buenas son las oportunidades disponibles para ese perfil
- Responde SOLO con el JSON, sin texto adicional antes ni después

FORMATO DE RESPUESTA (JSON estricto):
{
  "ranked_jobs": [
    {
      "job_id": "string",
      "match_score": 0.0,
      "match_reason": "Explicación personalizada...",
      "highlights": ["Badge 1", "Badge 2", "Badge 3"]
    }
  ],
  "agent_summary": "Resumen del panorama laboral para este perfil..."
}"""


# ── User prompt: reranking ────────────────────────────────────────────────────
def reranking_user(user: dict, candidates: list[dict], top_k: int = 10) -> str:
    """
    Construye el prompt de usuario para que el LLM reranquee las vacantes.

    Args:
        user:       perfil del candidato (dict)
        candidates: lista de hasta 20 vacantes candidatas del Vector DB
        top_k:      cuántas devolver en el resultado final
    """
    # Formatear perfil del usuario de forma compacta
    skills_str = ", ".join(user.get("skills", [])) or "No especificadas"
    salary = user.get("desired_salary")
    salary_str = f"${salary:,} COP" if salary else "No especificada"

    profile_block = f"""PERFIL DEL CANDIDATO:
- Nombre: {user.get('full_name', 'Candidato')}
- Cargo actual: {user.get('current_title', 'No especificado')}
- Empresa actual: {user.get('current_company') or 'Desempleado / En búsqueda'}
- Años de experiencia: {user.get('experience_years', 0)}
- Nivel educativo: {user.get('education_level', 'No especificado')}
- Ciudad: {user.get('city', 'No especificada')}
- Skills: {skills_str}
- Salario deseado: {salary_str}"""

    # Formatear candidatos de forma compacta para no exceder tokens
    candidates_lines = []
    for i, job in enumerate(candidates, 1):
        salary_range = ""
        if job.get("salary_min") and job.get("salary_max"):
            salary_range = f"${job['salary_min']:,}–${job['salary_max']:,} COP"
        elif job.get("salary_min"):
            salary_range = f"desde ${job['salary_min']:,} COP"

        skills_req = ", ".join(job.get("skills_required", [])[:6]) or "No especificadas"

        candidates_lines.append(
            f"{i}. [{job['job_id']}] {job['title']} @ {job['company']} | "
            f"{job.get('city', '?')} | {job.get('modality', '?')} | "
            f"{salary_range} | Exp: {job.get('experience_years', 0)}+ años | "
            f"Skills: {skills_req}"
        )

    candidates_block = "VACANTES CANDIDATAS (pre-filtradas por relevancia semántica):\n" + "\n".join(candidates_lines)

    instruction = f"""
TAREA:
1. Analiza qué tan bien encaja el perfil del candidato con cada vacante
2. Selecciona y rankea las MEJORES {top_k} vacantes (o menos si no hay suficientes con score ≥ 0.40)
3. Para cada una: asigna match_score, escribe match_reason personalizado y genera highlights
4. Escribe el agent_summary

Recuerda: responde SOLO con el JSON, sin texto adicional."""

    return f"{profile_block}\n\n{candidates_block}\n{instruction}"
