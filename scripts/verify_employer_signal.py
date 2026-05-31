"""
Tests del Employer Signal Agent
────────────────────────────────────────────────────────────────────────
Sin base de datos ni llamadas reales a Claude.

Ejecutar:
    python3 scripts/verify_employer_signal.py
"""
from __future__ import annotations
import asyncio, json, sys, types
from datetime import datetime, timezone
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
            "content": "¡Andrés, Rappi revisó tu perfil hoy! Están buscando Backend Python. ¡Es tu momento!",
            "usage": {"total_tokens": 50, "cost_usd": 0.001, "prompt_tokens": 40, "completion_tokens": 10},
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

# ── Imports ───────────────────────────────────────────────────────────────────
from agents.employer_signal.agent  import EmployerSignalAgent, _MOCK_COMPANIES
from agents.employer_signal.models import (
    BatchSignalResult, EmployerView, SignalResult, SimulateRequest,
)
from agents.employer_signal.prompts import EMPLOYER_SIGNAL_SYSTEM, employer_signal_prompt
from cdp.events import Events

# ── Helpers ───────────────────────────────────────────────────────────────────
PASSED = FAILED = 0
def ok(n):      global PASSED; PASSED += 1; print(f"  ✅ {n}")
def fail(n, r): global FAILED; FAILED += 1; print(f"  ❌ {n}: {r}")
def section(t): print(f"\n{'─'*55}\n  {t}\n{'─'*55}")

def _user():
    return {"id": "550e8400-e29b-41d4-a716-000000000020",
            "full_name": "Andrés García", "email": "andres@example.com",
            "city": "Bogotá", "current_title": "Dev Backend Jr"}

def _view():
    return {"company_name": "Rappi", "company_category": "tecnologia",
            "job_title_viewed": "Desarrollador Backend", "view_duration_seconds": 87}

# ── Tests ─────────────────────────────────────────────────────────────────────
section("1. Modelos")

def test_employer_view():
    try:
        v = EmployerView(user_id="u1", company_name="Rappi",
                         viewed_at=datetime.now(timezone.utc))
        assert v.company_name == "Rappi"
        assert v.view_duration_seconds == 0
        ok("EmployerView crea con defaults correctos")
    except Exception as e: fail("EmployerView", str(e))

def test_signal_result():
    try:
        r = SignalResult(user_id="u1", full_name="Ana", company_name="Rappi",
                         channel="log", success=True)
        assert r.message_id is None
        ok("SignalResult crea correctamente")
    except Exception as e: fail("SignalResult", str(e))

def test_batch_empty():
    try:
        b = BatchSignalResult.empty()
        assert b.total_processed == 0
        ok("BatchSignalResult.empty() funciona")
    except Exception as e: fail("BatchSignalResult.empty", str(e))

test_employer_view()
test_signal_result()
test_batch_empty()

section("2. Prompts")

def test_system_prompt():
    try:
        assert "empresa" in EMPLOYER_SIGNAL_SYSTEM.lower() or "perfil" in EMPLOYER_SIGNAL_SYSTEM.lower()
        assert "40" in EMPLOYER_SIGNAL_SYSTEM or "50" in EMPLOYER_SIGNAL_SYSTEM  # límite palabras
        ok("EMPLOYER_SIGNAL_SYSTEM contiene instrucciones correctas")
    except Exception as e: fail("EMPLOYER_SIGNAL_SYSTEM", str(e))

def test_prompt_builder():
    try:
        msg = employer_signal_prompt(_user(), _view())
        assert "Andrés García" in msg
        assert "Rappi" in msg
        assert "87" in msg  # duración
        assert "Desarrollador Backend" in msg
        ok("employer_signal_prompt incluye todos los datos clave")
    except Exception as e: fail("employer_signal_prompt", str(e))

def test_prompt_sin_job():
    try:
        view = {"company_name": "Bancolombia", "view_duration_seconds": 45,
                "job_title_viewed": "", "company_category": "finanzas"}
        msg = employer_signal_prompt(_user(), view)
        assert "Bancolombia" in msg
        ok("employer_signal_prompt funciona sin job_title_viewed")
    except Exception as e: fail("employer_signal_prompt sin job", str(e))

def test_prompt_duracion_mensaje():
    try:
        view_long = {**_view(), "view_duration_seconds": 120}
        msg_long = employer_signal_prompt(_user(), view_long)
        view_short = {**_view(), "view_duration_seconds": 20}
        msg_short = employer_signal_prompt(_user(), view_short)
        # Long duration gets a special hint
        assert "2 min" in msg_long or "detenimiento" in msg_long
        ok("employer_signal_prompt añade hint especial para visitas largas (>60s)")
    except Exception as e: fail("employer_signal_prompt duración", str(e))

test_system_prompt()
test_prompt_builder()
test_prompt_sin_job()
test_prompt_duracion_mensaje()

section("3. Agente — sin pool/CDP")

async def test_process_pending_sin_pool():
    try:
        agent = EmployerSignalAgent()
        r = await agent.process_pending()
        assert r.total_processed == 0
        ok("process_pending sin pool retorna batch vacío")
    except Exception as e: fail("process_pending sin pool", str(e))

async def test_simulate_sin_cdp():
    try:
        agent = EmployerSignalAgent()
        count = await agent.simulate_employer_views(n=3)
        assert count == 0  # sin CDP no puede insertar
        ok("simulate_employer_views sin CDP retorna 0")
    except Exception as e: fail("simulate sin CDP", str(e))

async def test_is_already_notified_sin_pool():
    try:
        agent = EmployerSignalAgent()
        result = await agent._is_already_notified("user-1", "Rappi")
        assert result is False
        ok("_is_already_notified sin pool retorna False")
    except Exception as e: fail("_is_already_notified sin pool", str(e))

asyncio.run(test_process_pending_sin_pool())
asyncio.run(test_simulate_sin_cdp())
asyncio.run(test_is_already_notified_sin_pool())

section("4. Agente — con mocks")

async def test_process_signal_completo():
    try:
        conn_mock = MagicMock()
        # _get_pending_signals → 1 señal
        conn_mock.fetch = AsyncMock(return_value=[{
            "user_id": _user()["id"],
            "properties": json.dumps({"company_name": "Rappi", "job_title_viewed": "Dev Backend",
                                       "view_duration_seconds": 87, "company_category": "tecnologia"}),
            "viewed_at": datetime.now(timezone.utc),
        }])
        # _is_already_notified → False
        conn_mock.fetchval = AsyncMock(return_value=False)
        # _get_user_profile → usuario
        conn_mock.fetchrow = AsyncMock(return_value=_user())
        conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
        conn_mock.__aexit__  = AsyncMock(return_value=None)
        pool_mock = MagicMock(); pool_mock.acquire = MagicMock(return_value=conn_mock)
        cdp_mock = MagicMock(); cdp_mock.track = AsyncMock(return_value="event-id")

        agent = EmployerSignalAgent(cdp=cdp_mock, pool=pool_mock)
        result = await agent.process_pending(window_minutes=15)

        assert result.sent_ok == 1
        assert result.total_processed == 1
        assert cdp_mock.track.called
        ok("process_pending con mock procesa señal, genera notificación y trackea")
    except Exception as e: fail("process_pending mock completo", str(e))

async def test_signal_ya_notificado():
    try:
        conn_mock = MagicMock()
        conn_mock.fetch = AsyncMock(return_value=[{
            "user_id": "user-1",
            "properties": json.dumps({"company_name": "Rappi"}),
            "viewed_at": datetime.now(timezone.utc),
        }])
        conn_mock.fetchval = AsyncMock(return_value=True)  # ya notificado
        conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
        conn_mock.__aexit__  = AsyncMock(return_value=None)
        pool_mock = MagicMock(); pool_mock.acquire = MagicMock(return_value=conn_mock)

        agent = EmployerSignalAgent(pool=pool_mock)
        result = await agent.process_pending()
        assert result.skipped == 1
        assert result.sent_ok == 0
        ok("process_pending omite candidatos ya notificados en 24h")
    except Exception as e: fail("signal ya notificado", str(e))

asyncio.run(test_process_signal_completo())
asyncio.run(test_signal_ya_notificado())

section("5. Simulador")

def test_mock_companies_disponibles():
    try:
        assert len(_MOCK_COMPANIES) >= 5
        for c in _MOCK_COMPANIES:
            assert "name" in c
            assert "category" in c
            assert "job" in c
        ok(f"_MOCK_COMPANIES tiene {len(_MOCK_COMPANIES)} empresas con estructura correcta")
    except Exception as e: fail("_MOCK_COMPANIES", str(e))

async def test_simulate_con_mocks():
    try:
        conn_mock = MagicMock()
        conn_mock.fetch = AsyncMock(return_value=[_user(), _user()])
        conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
        conn_mock.__aexit__  = AsyncMock(return_value=None)
        pool_mock = MagicMock(); pool_mock.acquire = MagicMock(return_value=conn_mock)
        cdp_mock  = MagicMock(); cdp_mock.track = AsyncMock(return_value="event-id")

        agent = EmployerSignalAgent(cdp=cdp_mock, pool=pool_mock)
        count = await agent.simulate_employer_views(n=2)
        assert count == 2
        assert cdp_mock.track.call_count == 2
        ok("simulate_employer_views inserta eventos en el CDP para cada usuario")
    except Exception as e: fail("simulate_employer_views mock", str(e))

test_mock_companies_disponibles()
asyncio.run(test_simulate_con_mocks())

section("6. CDP Events")

def test_events_definidos():
    try:
        assert Events.EMPLOYER_VIEWED_PROFILE == "employer.viewed_profile"
        assert Events.EMPLOYER_SIGNAL_SENT    == "employer.signal_notified"
        ok("Events.EMPLOYER_VIEWED_PROFILE y EMPLOYER_SIGNAL_SENT definidos")
    except Exception as e: fail("Events employer", str(e))

test_events_definidos()

section("7. Scheduler")

async def test_scheduler_stats():
    try:
        from agents.employer_signal.scheduler import EmployerSignalScheduler
        s = EmployerSignalScheduler(cdp=MagicMock(), pool=MagicMock(), agent=MagicMock())
        stats = s.stats()
        assert stats["interval_s"] == 900   # 15 minutos
        assert stats["running"] is False
        ok("EmployerSignalScheduler inicializa con intervalo de 15 min (900s)")
    except Exception as e: fail("EmployerSignalScheduler", str(e))

asyncio.run(test_scheduler_stats())

# ── Resultado ─────────────────────────────────────────────────────────────────
total = PASSED + FAILED
print(f"\n{'═'*55}\n  Resultado: {PASSED}/{total} tests pasaron\n{'═'*55}\n")
if FAILED > 0:
    print(f"  ⚠️  {FAILED} test(s) fallaron"); sys.exit(1)
else:
    print("  ✅ Employer Signal Agent verificado correctamente"); sys.exit(0)
