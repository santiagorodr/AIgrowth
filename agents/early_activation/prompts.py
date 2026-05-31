"""
Prompts del Early Activation Agent
────────────────────────────────────
Un prompt por cada paso de la secuencia de 72 horas.
Cada función recibe el contexto del usuario y retorna el prompt
que Claude usará para generar el mensaje personalizado.

Principios de diseño:
  - Tono: cálido, colombiano, motivador — NUNCA spam corporativo
  - Siempre mencionar algo específico del perfil (no plantillas genéricas)
  - WhatsApp: texto plano, máx 3 párrafos, emojis moderados
  - Email: puede ser más largo, con HTML básico si se necesita
"""

from __future__ import annotations

# ── System prompt compartido ──────────────────────────────────────────────────
ACTIVATION_SYSTEM = """Eres el asistente de activación de elempleo, el portal de empleo líder en Colombia.

Tu misión es generar mensajes personalizados para nuevos usuarios que acaban de registrarse, ayudándolos a dar su primer paso en su búsqueda de empleo.

REGLAS:
- Habla en español colombiano natural y cercano (tutéalo, usa "te" no "le")
- Sé específico: menciona el cargo, la ciudad y/o las skills del usuario
- Tono motivador pero honesto — nunca prometidas vacías
- WhatsApp: máx 160 palabras, párrafos cortos, 2-3 emojis máximo
- Email asunto: máx 60 caracteres, llamativo, con emoji al inicio
- Email cuerpo: 150-250 palabras, claro y directo
- Responde ÚNICAMENTE con el JSON solicitado, sin texto adicional

FORMATO DE RESPUESTA:
{
  "subject": "Asunto del email o título del push (máx 60 chars)",
  "email_body": "Cuerpo del email en texto plano (no HTML)",
  "whatsapp_text": "Versión corta para WhatsApp (máx 160 palabras)"
}"""


# ── Step 1: Welcome (H+0) ─────────────────────────────────────────────────────
def welcome_prompt(user: dict, top_jobs: list[dict]) -> str:
    name      = user.get("full_name", "").split()[0] or "candidato"
    title     = user.get("current_title", "profesional")
    city      = user.get("city", "Colombia")
    source    = user.get("source", "organic")

    source_context = {
        "whatsapp": "llegaste a través de WhatsApp",
        "referral":  "un amigo te recomendó elempleo",
        "paid_meta": "encontraste elempleo en Instagram o Facebook",
        "seo":       "nos encontraste buscando en Google",
    }.get(source, "acabas de registrarte en elempleo")

    jobs_preview = "\n".join(
        f"  - {j.get('title', '?')} en {j.get('company', '?')} "
        f"({j.get('city', city)}, {j.get('modality', '?')})"
        for j in top_jobs[:3]
    ) if top_jobs else "  - Vacantes relevantes para tu perfil cargando..."

    return f"""CONTEXTO:
- Usuario: {name}, {title} en {city}
- Cómo llegó: {source_context}
- Top 3 vacantes que encontramos para él/ella:
{jobs_preview}

TAREA:
Genera el mensaje de bienvenida. Debe:
1. Saludar por el nombre y mencionar que {source_context}
2. Presentar las 3 vacantes de forma atractiva (no como lista aburrida)
3. Invitar a completar el perfil para ver más oportunidades
4. Terminar con un CTA claro: "Ver todas mis vacantes →"

Recuerda el formato JSON con subject, email_body y whatsapp_text."""


# ── Step 2: CV Tip (H+2) ──────────────────────────────────────────────────────
def cv_tip_prompt(user: dict, profile_completion: int) -> str:
    name       = user.get("full_name", "").split()[0] or "candidato"
    title      = user.get("current_title", "profesional")
    skills     = user.get("skills", [])
    completion = profile_completion

    # Tip específico según qué falta en el perfil
    if completion < 40:
        missing_element = "foto de perfil y descripción profesional"
        impact = "los reclutadores tienen 3x más probabilidad de contactarte si tienes foto"
    elif completion < 60:
        missing_element = "tu experiencia laboral detallada"
        impact = "las empresas necesitan ver tu trayectoria para considerarte"
    elif completion < 80:
        missing_element = "tus estudios y certificaciones"
        impact = "el 70% de vacantes filtra por nivel educativo"
    else:
        missing_element = "tus expectativas salariales y disponibilidad"
        impact = "esto te muestra en vacantes que ya cuadran con lo que buscas"

    skills_str = ", ".join(skills[:4]) if skills else "las skills de tu área"

    return f"""CONTEXTO:
- Usuario: {name}, {title}
- Perfil completado: {completion}%
- Skills registradas: {skills_str}
- Elemento que falta: {missing_element}
- Impacto de completarlo: {impact}

TAREA:
Genera un mensaje de WhatsApp (prioritario) y email de acompañamiento.
El mensaje debe:
1. Felicitarlo/la por registrarse (reconocer la acción)
2. Darle UN tip concreto y específico: agregar {missing_element}
3. Explicar el beneficio real: {impact}
4. Ser breve y conversacional — como si un amigo le diera el tip

El tono es de acompañamiento, no de instrucción corporativa.
Recuerda el formato JSON."""


# ── Step 3: Employer Signal (H+24) ───────────────────────────────────────────
def employer_signal_prompt(user: dict, demand_data: dict, new_jobs: list[dict]) -> str:
    name     = user.get("full_name", "").split()[0] or "candidato"
    title    = user.get("current_title", "profesional")
    city     = user.get("city", "Colombia")
    category = user.get("category", "tu área")

    companies_count = demand_data.get("companies_hiring", 12)
    new_jobs_week   = demand_data.get("new_jobs_this_week", 47)
    avg_salary      = demand_data.get("avg_salary_range", "")

    jobs_list = "\n".join(
        f"  {i+1}. {j.get('title','?')} en {j.get('company','?')} — "
        f"{j.get('modality','presencial')}"
        for i, j in enumerate(new_jobs[:5])
    ) if new_jobs else "  Vacantes frescas de esta semana disponibles en tu perfil."

    salary_line = f"- Salario promedio del sector: {avg_salary}" if avg_salary else ""

    return f"""CONTEXTO:
- Usuario: {name}, {title} en {city}
- Categoría laboral: {category}
- Datos del mercado esta semana en {city}:
  * {companies_count} empresas publicaron vacantes en {category}
  * {new_jobs_week} vacantes nuevas en tu área
  {salary_line}
- Nuevas vacantes para mostrarle:
{jobs_list}

TAREA:
Genera el mensaje de señal de demanda. Debe:
1. Abrir con un dato del mercado llamativo (ej: "{companies_count} empresas en {city} buscan perfiles como el tuyo")
2. Crear sentido de urgencia real (no artificial) — las vacantes se llenan
3. Presentar las vacantes nuevas de forma atractiva
4. CTA: "Ver vacantes nuevas →"

El email es el canal principal, pero también genera versión WhatsApp.
Recuerda el formato JSON."""


# ── Step 4: First Apply Nudge (H+48) ─────────────────────────────────────────
def first_apply_nudge_prompt(user: dict, top_jobs: list[dict]) -> str:
    name   = user.get("full_name", "").split()[0] or "candidato"
    title  = user.get("current_title", "profesional")
    city   = user.get("city", "Colombia")
    skills = user.get("skills", [])[:3]

    skills_str = " y ".join(skills) if skills else "tus habilidades"

    jobs_list = "\n".join(
        f"  {i+1}. {j.get('title','?')} @ {j.get('company','?')} "
        f"({j.get('modality','?')}) — score {j.get('match_score', 0.8):.0%} de match"
        for i, j in enumerate(top_jobs[:5])
    ) if top_jobs else "  Tus top vacantes están esperándote en elempleo."

    return f"""CONTEXTO:
- Usuario: {name}, {title} en {city}
- Skills clave: {skills_str}
- Ha pasado 48 horas desde su registro y aún no ha aplicado
- Top vacantes para él/ella:
{jobs_list}

TAREA:
Genera un mensaje de WhatsApp (canal principal) y email de apoyo.
Tono: amigable, directo, motivador — NO presionante ni culpabilizador.
El mensaje debe:
1. Reconocer que probablemente está evaluando opciones (empatía)
2. Señalar que con {skills_str} tiene buenas opciones esperando
3. Presentar las vacantes de forma visual/atractiva
4. CTA directo: "Aplicar ahora toma menos de 2 minutos →"

Mensaje WhatsApp: máx 130 palabras. Email: más detallado con las vacantes.
Recuerda el formato JSON."""


# ── Step 5: Reactivation Check (H+72) ────────────────────────────────────────
def reactivation_check_prompt(user: dict) -> str:
    name  = user.get("full_name", "").split()[0] or "candidato"
    title = user.get("current_title", "profesional")
    city  = user.get("city", "Colombia")

    return f"""CONTEXTO:
- Usuario: {name}, {title} en {city}
- Han pasado 72 horas desde su registro sin actividad significativa
- No ha completado el perfil ni aplicado a vacantes

TAREA:
Genera un email empático de "check-in". NO es un email agresivo de reactivación.
El tono es: "Queremos ayudarte, ¿qué necesitas?"

El mensaje debe:
1. Reconocer que encontrar empleo puede ser abrumador (sin culpar)
2. Ofrecer ayuda concreta: el Career Copilot puede revisar su CV gratis
3. Preguntar si tiene algún problema o duda específica
4. Dar opciones: "Ver vacantes" | "Mejorar mi CV" | "Hablar con un asesor"

Email principal. WhatsApp opcional (más corto).
Recuerda el formato JSON."""
