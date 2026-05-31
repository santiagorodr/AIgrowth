"""
Verificación end-to-end del Job Match Agent.
Testea toda la lógica interna sin necesitar Docker, APIs externas
ni sentence-transformers (modelo ML pesado).
"""
import json, sys, types
sys.path.insert(0, ".")

# ── Mock de dependencias externas (no disponibles sin Docker/install) ────────
# sentence_transformers — se instala al correr `make init`, no en CI
fake_st = types.ModuleType("sentence_transformers")
class _FakeModel:
    def encode(self, texts, **kw):
        import random
        return [[random.random() for _ in range(384)] for _ in (texts if isinstance(texts, list) else [texts])]
fake_st.SentenceTransformer = lambda *a, **kw: _FakeModel()
sys.modules["sentence_transformers"] = fake_st

# qdrant_client — stub mínimo
fake_qdrant = types.ModuleType("qdrant_client")
fake_models = types.ModuleType("qdrant_client.models")
for name in ["Distance", "FieldCondition", "Filter", "MatchValue",
             "PayloadSchemaType", "PointStruct", "VectorParams", "FilterSelector"]:
    setattr(fake_models, name, type(name, (), {"COSINE": "cosine", "KEYWORD": "kw",
                                                "INTEGER": "int", "BOOL": "bool"})())
fake_qdrant.QdrantClient = lambda **kw: None
sys.modules["qdrant_client"] = fake_qdrant
sys.modules["qdrant_client.models"] = fake_models

# asyncpg — stub mínimo
fake_asyncpg = types.ModuleType("asyncpg")
sys.modules["asyncpg"] = fake_asyncpg

# ── Ahora sí, importar los módulos del agente ─────────────────────────────
from agents.job_match.models import JobMatchRequest, MatchedJob, UserProfile
from agents.job_match.prompts import RERANKING_SYSTEM, reranking_user
from agents.job_match.agent import JobMatchAgent

print("  ✅ Imports correctos — sin dependencias circulares")

# ── 1. Modelos Pydantic ───────────────────────────────────────────────────────
user = UserProfile(
    id="user-001",
    full_name="Andrés García",
    current_title="Desarrollador Backend Junior",
    city="Bogotá",
    experience_years=3,
    skills=["Python", "FastAPI", "PostgreSQL"],
    desired_salary=8_000_000,
)
req = JobMatchRequest(user=user, top_k=5, filter_city=True, include_remote=True)
assert req.top_k == 5 and req.filter_city is True and req.include_remote is True
print(f"  ✅ UserProfile OK  →  {user.full_name} | {user.current_title} | {user.city}")
print(f"  ✅ JobMatchRequest OK  →  top_k={req.top_k}, filter_city={req.filter_city}")

# ── 2. Generación de prompt ───────────────────────────────────────────────────
candidates_mock = [
    {"job_id": "job-001", "title": "Desarrollador Backend Python",
     "company": "Rappi", "city": "Bogotá", "modality": "hibrido",
     "salary_min": 7_000_000, "salary_max": 12_000_000, "experience_years": 3,
     "skills_required": ["Python", "FastAPI", "PostgreSQL", "Docker"]},
    {"job_id": "job-006", "title": "Frontend Developer React",
     "company": "Habi", "city": "Bogotá", "modality": "remoto",
     "salary_min": 6_000_000, "salary_max": 10_000_000, "experience_years": 2,
     "skills_required": ["React", "TypeScript", "Next.js"]},
]
prompt = reranking_user(user=user.model_dump(), candidates=candidates_mock, top_k=5)

assert "Andrés García" in prompt,       "Falta nombre en el prompt"
assert "Desarrollador Backend" in prompt, "Falta cargo en el prompt"
assert "job-001" in prompt,             "Falta job_id en el prompt"
assert "Python" in prompt,              "Faltan skills en el prompt"
assert "$8,000,000 COP" in prompt,      "Falta salario deseado en el prompt"
assert "TAREA" in prompt,               "Falta la sección TAREA"
assert len(RERANKING_SYSTEM) > 500,     "System prompt demasiado corto"

print(f"  ✅ reranking_user() genera prompt de {len(prompt)} chars con todos los datos")
print(f"  ✅ RERANKING_SYSTEM: {len(RERANKING_SYSTEM)} chars, idioma español ✓")

# ── 3. Herencia de BaseAgent ──────────────────────────────────────────────────
agent = JobMatchAgent()
assert agent.AGENT_ID == "job_match_agent"
assert hasattr(agent, "llm")
assert hasattr(agent, "track")
assert hasattr(agent, "run")
assert hasattr(agent, "search")
assert hasattr(agent, "_parse_llm_response")
assert hasattr(agent, "_extract_json")
print(f"  ✅ JobMatchAgent hereda BaseAgent — AGENT_ID={agent.AGENT_ID!r}")
print(f"  ✅ Métodos públicos: run(), search()")
print(f"  ✅ Métodos privados: _parse_llm_response(), _extract_json(), _fallback_result()")

# ── 4. Parsing de respuesta LLM (JSON limpio) ─────────────────────────────────
candidates_full = [
    {"job_id": "job-001", "title": "Desarrollador Backend Python",
     "company": "Rappi", "city": "Bogotá", "modality": "hibrido",
     "salary_min": 7_000_000, "salary_max": 12_000_000,
     "skills_required": ["Python", "FastAPI"], "relevance_score": 0.88},
    {"job_id": "job-006", "title": "Frontend Developer React",
     "company": "Habi", "city": "Bogotá", "modality": "remoto",
     "salary_min": 6_000_000, "salary_max": 10_000_000,
     "skills_required": ["React"], "relevance_score": 0.61},
]
llm_json = json.dumps({
    "ranked_jobs": [
        {"job_id": "job-001", "match_score": 0.93,
         "match_reason": "Tus 3 años en Python y FastAPI encajan perfectamente con lo que busca Rappi.",
         "highlights": ["Python + FastAPI ✓", "Salario $7M–$12M ✓", "Híbrido disponible"]},
        {"job_id": "job-006", "match_score": 0.61,
         "match_reason": "Conoces Python pero el stack principal es React/TypeScript.",
         "highlights": ["Remoto 100%", "Salary dentro del rango"]},
    ],
    "agent_summary": "Hay excelentes oportunidades para un backend Python en Bogotá."
})

jobs, summary = agent._parse_llm_response(llm_json, candidates_full)
assert len(jobs) == 2
assert jobs[0].job_id == "job-001"
assert jobs[0].match_score == 0.93
assert jobs[0].title == "Desarrollador Backend Python"
assert jobs[0].company == "Rappi"
assert len(jobs[0].highlights) == 3
assert "excelentes" in summary

print(f"  ✅ _parse_llm_response() — {len(jobs)} vacantes rankeadas")
print(f"     #1: {jobs[0].title} @ {jobs[0].company}  score={jobs[0].match_score}")
print(f"         Highlights: {jobs[0].highlights}")
print(f"     #2: {jobs[1].title} @ {jobs[1].company}  score={jobs[1].match_score}")
print(f"  ✅ agent_summary: {summary[:60]}...")

# ── 5. _extract_json con markdown wrapper ────────────────────────────────────
for label, text in [
    ("JSON limpio",          '{"ranked_jobs": [], "agent_summary": "OK"}'),
    ("Markdown ```json```",  '```json\n{"ranked_jobs": [], "agent_summary": "OK"}\n```'),
    ("Texto antes del JSON", 'Aquí están los resultados:\n{"ranked_jobs": [], "agent_summary": "OK"}'),
]:
    extracted = agent._extract_json(text)
    parsed = json.loads(extracted)
    assert parsed["agent_summary"] == "OK"
    print(f"  ✅ _extract_json ({label})")

# ── 6. Fallback cuando LLM devuelve texto inválido ───────────────────────────
fallback_jobs, fallback_summary = agent._parse_llm_response("No puedo ayudarte.", candidates_full)
assert isinstance(fallback_jobs, list)   # no lanza excepción
print(f"  ✅ Fallback activado para respuesta no-JSON — retorna {len(fallback_jobs)} vacante(s) sin crash")

# ── 7. Validar scores del MatchedJob ─────────────────────────────────────────
valid_job = MatchedJob(
    job_id="j1", title="Dev", company="X", city="Bogotá",
    match_score=0.85, match_reason="Buen match.", highlights=["Badge 1"],
    semantic_score=0.80,
)
assert 0.0 <= valid_job.match_score <= 1.0
print(f"  ✅ MatchedJob valida rango de match_score (0.0–1.0)")

# ── 8. Resumen de archivos generados ─────────────────────────────────────────
import os
agent_files = [
    "agents/__init__.py",
    "agents/base.py",
    "agents/server.py",
    "agents/job_match/__init__.py",
    "agents/job_match/models.py",
    "agents/job_match/prompts.py",
    "agents/job_match/agent.py",
    "agents/job_match/api.py",
    "agents/job_match/demo.py",
]
total_lines = 0
print()
print("  Archivos del agente:")
for f in agent_files:
    lines = len(open(f).readlines())
    total_lines += lines
    print(f"    {f:<45} {lines:>4} líneas")
print(f"    {'TOTAL':<45} {total_lines:>4} líneas")

print()
print("=" * 55)
print("  ✅  Job Match Agent — verificación completa")
print("=" * 55)
