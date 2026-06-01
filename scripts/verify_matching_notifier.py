"""
Tests del Matching Notifier Agent
────────────────────────────────────────────────────────────────────────
Sin base de datos ni llamadas reales a Claude/Qdrant.

Ejecutar:
    python3 scripts/verify_matching_notifier.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Mocks ANTES de importar el proyecto ──────────────────────────────────────
def _mock_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name); sys.modules[name] = mod; return mod

structlog_mod = _mock_module("structlog")
class _FakeLog:
    def __getattr__(self, n): return lambda *a, **kw: None
structlog_mod.get_logger = lambda *a, **kw: _FakeLog()
structlog_mod.configure  = lambda **kw: None

httpx_mod = _mock_module("httpx")
class _FakeResp:
    def raise_for_status(self): pass
    def json(self):
        return {
            "content": "Hola Andrés, Rappi está buscando un Backend Python en Bogotá — tu perfil tiene un 82% de compatibilidad. ¡Échale un vistazo!",
            "usage": {"total_tokens": 80, "cost_usd": 0.0002, "prompt_tokens": 60, "completion_tokens": 20},
        }
class _FakeHTTP:
    def __init__(self, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, *a, **kw): return _FakeResp()
    async def get(self, *a, **kw):  return _FakeResp()
    async def aclose(self): pass
    @property
    def is_closed(self): return False
httpx_mod.AsyncClient = _FakeHTTP
httpx_mod.HTTPStatusError = Exception
httpx_mod.ConnectError    = Exception

_mock_module("asyncpg")

# Mock sentence_transformers para que no cargue el modelo
st_mod = _mock_module("sentence_transformers")
class _FakeST:
    def __init__(self, *a, **kw): pass
    def encode(self, texts, **kw):
        import numpy as np
        return np.zeros((len(texts), 384))
st_mod.SentenceTransformer = _FakeST

# Mock qdrant_client
qc_mod = _mock_module("qdrant_client")
qc_mod.QdrantClient = MagicMock
qm_mod = _mock_module("qdrant_client.models")
for cls in ["Filter","FieldCondition","MatchValue","PointStruct","Distance","VectorParams","PayloadSchemaType","FilterSelector"]:
    setattr(qm_mod, cls, MagicMock)

# ── Imports del proyecto ──────────────────────────────────────────────────────
from agents.matching_notifier.agent   import MatchingNotifierAgent, MATCH_THRESHOLD
from agents.matching_notifier.models  import (
    BatchNotificationResult, JobNotificationResult, MatchedUser, ProcessJobsRequest,
)
from agents.matching_notifier.prompts import MATCHING_SYSTEM, matching_prompt
from cdp.events import Events

# ── Helpers ───────────────────────────────────────────────────────────────────
PASSED = FAILED = 0
def ok(n):   global PASSED; PASSED += 1; print(f"  ✅ {n}")
def fail(n, r): global FAILED; FAILED += 1; print(f"  ❌ {n}: {r}")
def section(t): print(f"\n{'─'*55}\n  {t}\n{'─'*55}")

def _job():
    return {
        "id": "550e8400-e29b-41d4-a716-000000000001",
        "title": "Desarrollador Backend Python", "company": "Rappi",
        "city": "Bogotá", "category": "tecnologia", "modality": "hibrido",
        "contract_type": "indefinido", "salary_min": 7000000, "salary_max": 12000000,
        "experience_years": 3, "education_level": "profesional",
        "skills_required": ["Python", "FastAPI", "PostgreSQL"],
        "description": "Buscamos desarrollador backend con experiencia en Python.",
        "requirements": "3 años de experiencia.",
        "published_at": datetime.now(timezone.utc),
    }

def _user():
    return {
        "id": "550e8400-e29b-41d4-a716-000000000002",
        "full_name": "Andrés García", "email": "andres@example.com",
        "city": "Bogotá", "current_title": "Dev Backend Junior",
        "experience_years": 2, "skills": ["Python", "Django", "PostgreSQL"],
    }

# ── Tests ─────────────────────────────────────────────────────────────────────

section("1. Modelos")

def test_matched_user():
    try:
        u = MatchedUser(user_id="u1", full_name="Ana", match_score=0.82)
        assert u.notification_sent is False
        assert u.message_id is None
        ok("MatchedUser crea con defaults correctos")
    except Exception as e:
        fail("MatchedUser", str(e))

def test_job_notification_result():
    try:
        r = JobNotificationResult(
            job_id="j1", job_title="Dev Backend", company="Rappi",
            city="Bogotá", candidates_found=5, notified=3, skipped=2,
        )
        assert r.candidates_found == 5
        ok("JobNotificationResult crea correctamente")
    except Exception as e:
        fail("JobNotificationResult", str(e))

def test_batch_empty():
    try:
        b = BatchNotificationResult.empty()
        assert b.jobs_processed == 0
        assert b.total_notified == 0
        ok("BatchNotificationResult.empty() funciona")
    except Exception as e:
        fail("BatchNotificationResult.empty", str(e))

test_matched_user()
test_job_notification_result()
test_batch_empty()

section("2. Prompts")

def test_system_prompt():
    try:
        assert "3 líneas" in MATCHING_SYSTEM or "corta" in MATCHING_SYSTEM.lower()
        assert "español" in MATCHING_SYSTEM.lower() or "colombiano" in MATCHING_SYSTEM.lower()
        ok("MATCHING_SYSTEM contiene instrucciones de brevedad y tono")
    except Exception as e:
        fail("MATCHING_SYSTEM", str(e))

def test_matching_prompt_builder():
    try:
        msg = matching_prompt(_user(), _job(), score=0.82)
        assert "Andrés García" in msg
        assert "Rappi" in msg
        assert "82%" in msg
        assert "Python" in msg
        ok("matching_prompt incluye nombre, empresa, score y skills")
    except Exception as e:
        fail("matching_prompt", str(e))

def test_matching_prompt_sin_skills():
    try:
        user = _user(); user["skills"] = []
        job  = _job();  job["skills_required"] = []
        msg  = matching_prompt(user, job, score=0.5)
        assert "No especificadas" in msg
        ok("matching_prompt maneja skills vacías")
    except Exception as e:
        fail("matching_prompt sin skills", str(e))

test_system_prompt()
test_matching_prompt_builder()
test_matching_prompt_sin_skills()

section("3. Agente — sin pool/CDP")

async def test_process_new_jobs_sin_pool():
    try:
        agent = MatchingNotifierAgent()
        r = await agent.process_new_jobs(hours=24)
        assert r.jobs_processed == 0
        ok("process_new_jobs sin pool retorna batch vacío")
    except Exception as e:
        fail("process_new_jobs sin pool", str(e))

async def test_process_job_sin_usuarios():
    try:
        agent = MatchingNotifierAgent()
        # Mockear _find_matching_users para retornar lista vacía
        agent._find_matching_users = AsyncMock(return_value=[])
        r = await agent.process_job(_job())
        assert r.candidates_found == 0
        assert r.notified == 0
        ok("process_job sin candidatos retorna resultado vacío")
    except Exception as e:
        fail("process_job sin usuarios", str(e))

async def test_is_already_notified_sin_pool():
    try:
        agent = MatchingNotifierAgent()
        result = await agent._is_already_notified("user-1", "job-1")
        assert result is False   # sin pool → no duplicado
        ok("_is_already_notified sin pool retorna False")
    except Exception as e:
        fail("_is_already_notified sin pool", str(e))

asyncio.run(test_process_new_jobs_sin_pool())
asyncio.run(test_process_job_sin_usuarios())
asyncio.run(test_is_already_notified_sin_pool())

section("4. Agente — con mocks")

async def test_process_job_con_candidatos():
    try:
        # Pool mock
        conn_mock = MagicMock()
        conn_mock.fetchrow = AsyncMock(return_value=_user())
        conn_mock.fetchval = AsyncMock(return_value=False)  # no duplicado
        conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
        conn_mock.__aexit__  = AsyncMock(return_value=None)
        pool_mock = MagicMock()
        pool_mock.acquire = MagicMock(return_value=conn_mock)

        # CDP mock
        cdp_mock       = MagicMock()
        cdp_mock.track = AsyncMock(return_value="event-id")

        agent = MatchingNotifierAgent(cdp=cdp_mock, pool=pool_mock)

        # Mockear búsqueda Qdrant
        agent._find_matching_users = AsyncMock(return_value=[
            {"user_id": str(_user()["id"]), "relevance_score": 0.82,
             "full_name": "Andrés García", "city": "Bogotá", "current_title": "Dev Backend"},
        ])

        r = await agent.process_job(_job())
        assert r.candidates_found == 1
        assert r.notified == 1
        assert cdp_mock.track.called
        ok("process_job con candidato mock genera notificación y trackea evento")
    except Exception as e:
        fail("process_job con candidatos", str(e))

async def test_process_job_candidato_ya_notificado():
    try:
        conn_mock = MagicMock()
        conn_mock.fetchval = AsyncMock(return_value=True)  # ya notificado
        conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
        conn_mock.__aexit__  = AsyncMock(return_value=None)
        pool_mock = MagicMock()
        pool_mock.acquire = MagicMock(return_value=conn_mock)

        agent = MatchingNotifierAgent(pool=pool_mock)
        agent._find_matching_users = AsyncMock(return_value=[
            {"user_id": "user-1", "relevance_score": 0.80,
             "full_name": "Test", "city": "Bogotá", "current_title": "Dev"},
        ])

        r = await agent.process_job(_job())
        assert r.skipped == 1
        assert r.notified == 0
        ok("process_job omite candidatos ya notificados en ventana de dedup (72h por defecto)")
    except Exception as e:
        fail("process_job deduplicación", str(e))

asyncio.run(test_process_job_con_candidatos())
asyncio.run(test_process_job_candidato_ya_notificado())

section("5. Embedder — search_users")

def test_search_users_existe():
    try:
        from vector_db.embedder import JobEmbedder
        assert hasattr(JobEmbedder, "search_users"), "search_users no existe en JobEmbedder"
        import inspect
        sig = inspect.signature(JobEmbedder.search_users)
        assert "query" in sig.parameters
        assert "top_k" in sig.parameters
        assert "city" in sig.parameters
        assert "score_threshold" in sig.parameters
        ok("JobEmbedder.search_users existe con firma correcta")
    except Exception as e:
        fail("search_users firma", str(e))

test_search_users_existe()

section("6. CDP Events")

def test_event_definido():
    try:
        assert Events.MATCH_NOTIFICATION_SENT == "match.notification_sent"
        ok("Events.MATCH_NOTIFICATION_SENT definido correctamente")
    except Exception as e:
        fail("Events.MATCH_NOTIFICATION_SENT", str(e))

test_event_definido()

section("7. Scheduler")

async def test_scheduler_stats():
    try:
        from agents.matching_notifier.scheduler import MatchingScheduler
        s = MatchingScheduler(cdp=MagicMock(), pool=MagicMock(), agent=MagicMock())
        stats = s.stats()
        assert stats["ticks"] == 0
        assert stats["running"] is False
        assert stats["interval_s"] == 21600
        ok("MatchingScheduler inicializa con stats correctas (6h)")
    except Exception as e:
        fail("MatchingScheduler stats", str(e))

async def test_scheduler_stop():
    try:
        from agents.matching_notifier.scheduler import MatchingScheduler
        s = MatchingScheduler(cdp=MagicMock(), pool=MagicMock(), agent=MagicMock())
        s._running = True
        s.stop()
        assert s._running is False
        ok("MatchingScheduler.stop() detiene correctamente")
    except Exception as e:
        fail("MatchingScheduler stop", str(e))

asyncio.run(test_scheduler_stats())
asyncio.run(test_scheduler_stop())

# ── Resultado ─────────────────────────────────────────────────────────────────
total = PASSED + FAILED
print(f"\n{'═'*55}")
print(f"  Resultado: {PASSED}/{total} tests pasaron")
print(f"{'═'*55}\n")
if FAILED > 0:
    print(f"  ⚠️  {FAILED} test(s) fallaron"); sys.exit(1)
else:
    print("  ✅ Matching Notifier verificado correctamente"); sys.exit(0)
