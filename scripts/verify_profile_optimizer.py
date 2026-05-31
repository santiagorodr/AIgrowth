"""
Tests del Profile Optimizer Agent
────────────────────────────────────────────────────────────────────
Sin base de datos ni llamadas reales a Claude/Qdrant.

Ejecutar:
    python3 scripts/verify_profile_optimizer.py
"""
from __future__ import annotations
import asyncio, json, sys, types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Mocks ─────────────────────────────────────────────────────────────────────
def _mock_module(name):
    mod = types.ModuleType(name); sys.modules[name] = mod; return mod

structlog_mod = _mock_module("structlog")
class _FL:
    def __getattr__(self, n): return lambda *a, **kw: None
structlog_mod.get_logger = lambda *a, **kw: _FL()
structlog_mod.configure  = lambda **kw: None

httpx_mod = _mock_module("httpx")
class _FR:
    def raise_for_status(self): pass
    def json(self):
        return {
            "content": json.dumps({
                "projected_completion": 82,
                "summary": "Con 3 cambios tu perfil estará en el top 25%",
                "suggestions": [
                    {"priority": "high",   "field": "skills",   "current": "Python",
                     "suggested": "Agregar FastAPI, Docker",   "reason": "80% vacantes lo piden", "effort": "5 min"},
                    {"priority": "medium", "field": "title",    "current": "",
                     "suggested": "Dev Backend Jr — Python",   "reason": "Mejora visibilidad",    "effort": "5 min"},
                    {"priority": "low",    "field": "summary",  "current": "",
                     "suggested": "Agregar resumen profesional","reason": "Aumenta clics 40%",    "effort": "30 min"},
                ],
            }),
            "usage": {"total_tokens": 600, "cost_usd": 0.015, "prompt_tokens": 400, "completion_tokens": 200},
        }
class _FH:
    def __init__(self, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, *a, **kw): return _FR()
    async def get(self, *a, **kw):  return _FR()
    async def aclose(self): pass
    @property
    def is_closed(self): return False
httpx_mod.AsyncClient = _FH
httpx_mod.HTTPStatusError = Exception
httpx_mod.ConnectError    = Exception

_mock_module("asyncpg")

# Mock embedder
st_mod = _mock_module("sentence_transformers")
class _FST:
    def __init__(self, *a, **kw): pass
    def encode(self, t, **kw):
        import numpy as np; return np.zeros((len(t), 384))
st_mod.SentenceTransformer = _FST
qc_mod = _mock_module("qdrant_client"); qc_mod.QdrantClient = MagicMock
qm_mod = _mock_module("qdrant_client.models")
for c in ["Filter","FieldCondition","MatchValue","PointStruct","Distance","VectorParams","PayloadSchemaType","FilterSelector"]:
    setattr(qm_mod, c, MagicMock)

# ── Imports ───────────────────────────────────────────────────────────────────
from agents.profile_optimizer.agent  import ProfileOptimizerAgent
from agents.profile_optimizer.models import (
    BatchOptimizationResult, OptimizationReport,
    ProfileSuggestion, SuggestionPriority,
)
from agents.profile_optimizer.prompts import OPTIMIZER_SYSTEM, optimizer_prompt
from cdp.events import Events

# ── Helpers ───────────────────────────────────────────────────────────────────
PASSED = FAILED = 0
def ok(n):      global PASSED; PASSED += 1; print(f"  ✅ {n}")
def fail(n, r): global FAILED; FAILED += 1; print(f"  ❌ {n}: {r}")
def section(t): print(f"\n{'─'*55}\n  {t}\n{'─'*55}")

def _user(completion=45):
    return {
        "id": "550e8400-e29b-41d4-a716-000000000010",
        "full_name": "Carlos Méndez", "email": "carlos@example.com",
        "city": "Bogotá", "current_title": "Recién Graduado",
        "experience_years": 0, "education_level": "profesional",
        "skills": ["Python", "Java"], "profile_completion": completion,
        "desired_salary": 3000000,
    }

def _jobs():
    return [
        {"title": "Dev Backend Jr", "company": "Rappi", "city": "Bogotá",
         "modality": "hibrido", "experience_years": 1, "education_level": "profesional",
         "skills_required": ["Python", "FastAPI", "Docker"], "relevance_score": 0.82},
        {"title": "Desarrollador Python", "company": "Platzi", "city": "Bogotá",
         "modality": "remoto", "experience_years": 1, "education_level": "profesional",
         "skills_required": ["Python", "Django", "Git"], "relevance_score": 0.76},
    ]

# ── Tests ─────────────────────────────────────────────────────────────────────
section("1. Modelos")

def test_suggestion_priority_enum():
    try:
        assert SuggestionPriority.HIGH == "high"
        assert SuggestionPriority.MEDIUM == "medium"
        assert SuggestionPriority.LOW == "low"
        ok("SuggestionPriority enum correcto")
    except Exception as e: fail("SuggestionPriority", str(e))

def test_profile_suggestion():
    try:
        s = ProfileSuggestion(priority=SuggestionPriority.HIGH, field="skills",
                              current="Python", suggested="Agregar FastAPI",
                              reason="80% lo piden", effort="5 min")
        assert s.priority_emoji == "🔴"
        ok("ProfileSuggestion crea con emoji correcto")
    except Exception as e: fail("ProfileSuggestion", str(e))

def test_optimization_report():
    try:
        r = OptimizationReport(user_id="u1", full_name="Test",
                               current_completion=45, projected_completion=78,
                               suggestions=[
                                   ProfileSuggestion(priority=SuggestionPriority.HIGH, field="skills",
                                                     current="", suggested="X", reason="Y", effort="5 min"),
                               ])
        assert r.improvement == 33
        assert r.high_priority_count == 1
        ok("OptimizationReport calcula improvement y high_priority_count")
    except Exception as e: fail("OptimizationReport", str(e))

def test_batch_empty():
    try:
        b = BatchOptimizationResult.empty()
        assert b.total_analyzed == 0
        ok("BatchOptimizationResult.empty() funciona")
    except Exception as e: fail("BatchOptimizationResult.empty", str(e))

test_suggestion_priority_enum()
test_profile_suggestion()
test_optimization_report()
test_batch_empty()

section("2. Prompts")

def test_system_prompt():
    try:
        assert "JSON" in OPTIMIZER_SYSTEM
        assert "projected_completion" in OPTIMIZER_SYSTEM
        assert "suggestions" in OPTIMIZER_SYSTEM
        assert "priority" in OPTIMIZER_SYSTEM
        ok("OPTIMIZER_SYSTEM contiene estructura JSON requerida")
    except Exception as e: fail("OPTIMIZER_SYSTEM", str(e))

def test_optimizer_prompt_builder():
    try:
        msg = optimizer_prompt(_user(), _jobs())
        assert "Carlos Méndez" in msg
        assert "Rappi" in msg
        assert "FastAPI" in msg
        assert "45%" in msg
        ok("optimizer_prompt incluye usuario, vacantes y skills")
    except Exception as e: fail("optimizer_prompt", str(e))

def test_optimizer_prompt_sin_jobs():
    try:
        msg = optimizer_prompt(_user(), [])
        assert "No se encontraron" in msg
        ok("optimizer_prompt maneja lista de vacantes vacía")
    except Exception as e: fail("optimizer_prompt sin jobs", str(e))

test_system_prompt()
test_optimizer_prompt_builder()
test_optimizer_prompt_sin_jobs()

section("3. Agente — parse de sugerencias")

def test_parse_valid_json():
    try:
        agent = ProfileOptimizerAgent()
        raw = json.dumps({
            "projected_completion": 82,
            "summary": "Con 3 cambios estarás en el top 25%",
            "suggestions": [
                {"priority": "high",   "field": "skills",  "current": "Python",
                 "suggested": "Agregar FastAPI", "reason": "80% lo piden", "effort": "5 min"},
                {"priority": "medium", "field": "title",   "current": "",
                 "suggested": "Dev Backend Jr",  "reason": "Mejora visibilidad", "effort": "5 min"},
            ],
        })
        report = agent._parse_suggestions(raw, _user(), _jobs())
        assert report.projected_completion == 82
        assert len(report.suggestions) == 2
        assert report.suggestions[0].priority == SuggestionPriority.HIGH
        assert report.summary == "Con 3 cambios estarás en el top 25%"
        ok("_parse_suggestions parsea JSON válido de Sonnet")
    except Exception as e: fail("_parse_suggestions JSON válido", str(e))

def test_parse_json_con_texto():
    try:
        agent = ProfileOptimizerAgent()
        raw = 'Aquí mi análisis:\n{"projected_completion":75,"summary":"Mejora tu perfil","suggestions":[]}'
        report = agent._parse_suggestions(raw, _user(), _jobs())
        assert report.projected_completion == 75
        ok("_parse_suggestions extrae JSON aunque haya texto extra")
    except Exception as e: fail("_parse_suggestions texto extra", str(e))

def test_parse_fallback():
    try:
        agent = ProfileOptimizerAgent()
        user  = _user(completion=30); user["skills"] = []
        report = agent._parse_suggestions("respuesta inválida", user, _jobs())
        assert len(report.suggestions) > 0
        assert report.suggestions[0].priority == SuggestionPriority.HIGH
        ok("_parse_suggestions genera fallback útil cuando JSON falla")
    except Exception as e: fail("_parse_suggestions fallback", str(e))

def test_parse_projected_capped_at_100():
    try:
        agent = ProfileOptimizerAgent()
        raw = json.dumps({"projected_completion": 150, "summary": "x", "suggestions": []})
        report = agent._parse_suggestions(raw, _user(), _jobs())
        assert report.projected_completion <= 100
        ok("_parse_suggestions limita projected_completion a 100")
    except Exception as e: fail("_parse_suggestions cap 100", str(e))

test_parse_valid_json()
test_parse_json_con_texto()
test_parse_fallback()
test_parse_projected_capped_at_100()

section("4. Agente — sin pool/CDP")

async def test_analyze_batch_sin_pool():
    try:
        agent = ProfileOptimizerAgent()
        r = await agent.analyze_batch()
        assert r.total_analyzed == 0
        ok("analyze_batch sin pool retorna resultado vacío")
    except Exception as e: fail("analyze_batch sin pool", str(e))

async def test_is_recently_optimized_sin_pool():
    try:
        agent = ProfileOptimizerAgent()
        result = await agent._is_recently_optimized("user-1")
        assert result is False
        ok("_is_recently_optimized sin pool retorna False")
    except Exception as e: fail("_is_recently_optimized sin pool", str(e))

async def test_analyze_user_con_mocks():
    try:
        cdp_mock       = MagicMock()
        cdp_mock.track = AsyncMock(return_value="event-id")
        conn_mock = MagicMock()
        conn_mock.fetchval = AsyncMock(return_value=False)
        conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
        conn_mock.__aexit__  = AsyncMock(return_value=None)
        pool_mock = MagicMock(); pool_mock.acquire = MagicMock(return_value=conn_mock)

        agent = ProfileOptimizerAgent(cdp=cdp_mock, pool=pool_mock)
        agent._get_relevant_jobs = AsyncMock(return_value=_jobs())

        report = await agent.analyze_user(_user())
        assert isinstance(report, OptimizationReport)
        assert report.user_id == str(_user()["id"])
        assert len(report.suggestions) > 0
        assert cdp_mock.track.called
        ok("analyze_user con mocks genera reporte y trackea evento")
    except Exception as e: fail("analyze_user con mocks", str(e))

asyncio.run(test_analyze_batch_sin_pool())
asyncio.run(test_is_recently_optimized_sin_pool())
asyncio.run(test_analyze_user_con_mocks())

section("5. CDP Events")

def test_event_definido():
    try:
        assert Events.PROFILE_OPTIMIZATION_SENT == "profile.optimization_suggested"
        ok("Events.PROFILE_OPTIMIZATION_SENT definido correctamente")
    except Exception as e: fail("Events.PROFILE_OPTIMIZATION_SENT", str(e))

test_event_definido()

section("6. Scheduler")

async def test_scheduler_stats():
    try:
        from agents.profile_optimizer.scheduler import ProfileScheduler
        s = ProfileScheduler(cdp=MagicMock(), pool=MagicMock(), agent=MagicMock())
        stats = s.stats()
        assert stats["ticks"] == 0
        assert stats["interval_s"] == 86400   # 24h
        ok("ProfileScheduler inicializa con intervalo de 24h")
    except Exception as e: fail("ProfileScheduler stats", str(e))

asyncio.run(test_scheduler_stats())

# ── Resultado ─────────────────────────────────────────────────────────────────
total = PASSED + FAILED
print(f"\n{'═'*55}\n  Resultado: {PASSED}/{total} tests pasaron\n{'═'*55}\n")
if FAILED > 0:
    print(f"  ⚠️  {FAILED} test(s) fallaron"); sys.exit(1)
else:
    print("  ✅ Profile Optimizer verificado correctamente"); sys.exit(0)
