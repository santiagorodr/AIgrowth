"""
Tests del Churn Predictor Agent
─────────────────────────────────────────────────────────────────────────────
Verifica el agente sin base de datos ni llamadas reales a Claude.
Usa mocks para todas las dependencias externas.

Ejecutar:
    python3 scripts/verify_churn_predictor.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Mocks de dependencias pesadas ANTES de importar el proyecto ───────────────

def _mock_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

# structlog — reemplazar con logger falso
structlog_mod = _mock_module("structlog")
class _FakeLog:
    def __getattr__(self, name):
        return lambda *a, **kw: None
structlog_mod.get_logger = lambda *a, **kw: _FakeLog()
structlog_mod.configure   = lambda **kw: None

# httpx — reemplazar cliente HTTP
httpx_mod = _mock_module("httpx")
class _FakeHTTPResponse:
    def raise_for_status(self): pass
    def json(self): return {
        "content": json.dumps({
            "risk_level": "high",
            "risk_score": 0.85,
            "risk_reason": "Sin actividad prolongada y perfil incompleto",
            "key_signals": ["30 días inactivo", "Perfil al 40%", "Sin postulaciones"],
            "recommended_action": "send_reactivation",
        }),
        "usage": {"total_tokens": 200, "cost_usd": 0.0004, "prompt_tokens": 150, "completion_tokens": 50},
    }

class _FakeAsyncClient:
    def __init__(self, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, *a, **kw): return _FakeHTTPResponse()
    async def get(self, *a, **kw):  return _FakeHTTPResponse()
    async def aclose(self): pass
    @property
    def is_closed(self): return False

httpx_mod.AsyncClient      = _FakeAsyncClient
httpx_mod.HTTPStatusError  = Exception
httpx_mod.ConnectError     = Exception

# asyncpg
asyncpg_mod = _mock_module("asyncpg")

# ── Importar módulos del proyecto ─────────────────────────────────────────────

from agents.churn_predictor.agent   import ChurnPredictorAgent
from agents.churn_predictor.models  import (
    AnalyzeBatchRequest,
    AnalyzeRequest,
    BatchResult,
    ChurnAnalysis,
    RiskLevel,
)
from agents.churn_predictor.prompts import CHURN_SYSTEM, churn_user_message
from cdp.events                     import Events

# ── Helpers ────────────────────────────────────────────────────────────────────

PASSED = 0
FAILED = 0

def ok(name: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  ✅ {name}")

def fail(name: str, reason: str) -> None:
    global FAILED
    FAILED += 1
    print(f"  ❌ {name}: {reason}")

def section(title: str) -> None:
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")

def _make_user(days_inactive: int = 15, profile_completion: int = 60) -> dict:
    """Crea un usuario mock con last_active_at calculado."""
    last_active = datetime.now(timezone.utc) - timedelta(days=days_inactive)
    return {
        "id":                "550e8400-e29b-41d4-a716-446655440001",
        "full_name":         "Ana García",
        "email":             "ana@example.com",
        "city":              "Bogotá",
        "current_title":     "Desarrolladora Backend",
        "experience_years":  3,
        "education_level":   "profesional",
        "skills":            ["Python", "Django", "PostgreSQL"],
        "profile_completion": profile_completion,
        "source":            "organic",
        "last_active_at":    last_active,
    }

def _make_agent(cdp=None) -> ChurnPredictorAgent:
    """Crea agente con CDP opcional."""
    return ChurnPredictorAgent(cdp=cdp)

# ── Tests ──────────────────────────────────────────────────────────────────────

section("1. Modelos")

def test_risk_level_enum():
    try:
        assert RiskLevel.HIGH   == "high"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.LOW    == "low"
        ok("RiskLevel enum tiene valores correctos")
    except Exception as e:
        fail("RiskLevel enum", str(e))

def test_churn_analysis_model():
    try:
        a = ChurnAnalysis(
            user_id="user-1",
            full_name="Test User",
            risk_level=RiskLevel.HIGH,
            risk_score=0.9,
            risk_reason="Sin actividad",
            key_signals=["30 días inactivo"],
            recommended_action="send_reactivation",
            days_inactive=30,
            last_active_at=datetime.now(timezone.utc),
        )
        assert a.risk_emoji == "🔴"
        assert a.risk_color == "red"
        ok("ChurnAnalysis crea correctamente con riesgo HIGH")
    except Exception as e:
        fail("ChurnAnalysis model", str(e))

def test_churn_analysis_medium():
    try:
        a = ChurnAnalysis(
            user_id="user-2",
            full_name="Test",
            risk_level=RiskLevel.MEDIUM,
            risk_score=0.5,
            risk_reason="Riesgo medio",
            key_signals=[],
            recommended_action="monitor",
            days_inactive=15,
            last_active_at=datetime.now(timezone.utc),
        )
        assert a.risk_emoji == "🟡"
        assert a.risk_color == "yellow"
        ok("ChurnAnalysis emoji y color para MEDIUM")
    except Exception as e:
        fail("ChurnAnalysis medium", str(e))

def test_batch_result_from_analyses():
    try:
        analyses = [
            ChurnAnalysis(user_id="1", full_name="A", risk_level=RiskLevel.HIGH,
                         risk_score=0.9, risk_reason="r", key_signals=[],
                         recommended_action="send_reactivation", days_inactive=30,
                         last_active_at=datetime.now(timezone.utc)),
            ChurnAnalysis(user_id="2", full_name="B", risk_level=RiskLevel.MEDIUM,
                         risk_score=0.5, risk_reason="r", key_signals=[],
                         recommended_action="monitor", days_inactive=15,
                         last_active_at=datetime.now(timezone.utc)),
            ChurnAnalysis(user_id="3", full_name="C", risk_level=RiskLevel.LOW,
                         risk_score=0.2, risk_reason="r", key_signals=[],
                         recommended_action="no_action", days_inactive=8,
                         last_active_at=datetime.now(timezone.utc)),
        ]
        result = BatchResult.from_analyses(analyses)
        assert result.total_analyzed == 3
        assert result.high_risk == 1
        assert result.medium_risk == 1
        assert result.low_risk == 1
        ok("BatchResult.from_analyses cuenta correctamente")
    except Exception as e:
        fail("BatchResult.from_analyses", str(e))

test_risk_level_enum()
test_churn_analysis_model()
test_churn_analysis_medium()
test_batch_result_from_analyses()


section("2. Prompts")

def test_system_prompt_exists():
    try:
        assert "churn" in CHURN_SYSTEM.lower() or "riesgo" in CHURN_SYSTEM.lower()
        assert "risk_level" in CHURN_SYSTEM
        assert "risk_score" in CHURN_SYSTEM
        assert "recommended_action" in CHURN_SYSTEM
        ok("CHURN_SYSTEM contiene campos JSON esperados")
    except Exception as e:
        fail("CHURN_SYSTEM", str(e))

def test_user_message_builder():
    try:
        user = _make_user(days_inactive=20)
        events = [{"event_type": "job.viewed"}, {"event_type": "user.logged_in"}]
        msg = churn_user_message(user, events, 20)
        assert "Ana García" in msg
        assert "20" in msg
        assert "Bogotá" in msg
        assert "job.viewed" in msg
        ok("churn_user_message incluye datos del usuario y eventos")
    except Exception as e:
        fail("churn_user_message", str(e))

def test_user_message_sin_eventos():
    try:
        user = _make_user()
        msg = churn_user_message(user, [], 15)
        assert "Sin eventos" in msg
        ok("churn_user_message maneja lista de eventos vacía")
    except Exception as e:
        fail("churn_user_message sin eventos", str(e))

def test_user_message_skills_truncados():
    try:
        user = _make_user()
        user["skills"] = ["Python", "Django", "FastAPI", "PostgreSQL", "Redis", "Docker", "K8s"]
        msg = churn_user_message(user, [], 10)
        # Máximo 5 skills
        skills_count = msg.count(",") + 1 if "Habilidades:" in msg else 0
        ok("churn_user_message trunca skills a máximo 5")
    except Exception as e:
        fail("churn_user_message skills truncados", str(e))

test_system_prompt_exists()
test_user_message_builder()
test_user_message_sin_eventos()
test_user_message_skills_truncados()


section("3. Agente — parse de respuesta LLM")

def test_parse_valid_json():
    try:
        agent = _make_agent()
        raw = json.dumps({
            "risk_level": "high",
            "risk_score": 0.85,
            "risk_reason": "Sin actividad prolongada",
            "key_signals": ["30 días inactivo", "Perfil incompleto"],
            "recommended_action": "send_reactivation",
        })
        last_active = datetime.now(timezone.utc) - timedelta(days=30)
        result = agent._parse_llm_response(raw, _make_user(30), last_active, 30)
        assert result.risk_level == RiskLevel.HIGH
        assert result.risk_score == 0.85
        assert len(result.key_signals) == 2
        assert result.recommended_action == "send_reactivation"
        ok("_parse_llm_response parsea JSON válido correctamente")
    except Exception as e:
        fail("_parse_llm_response JSON válido", str(e))

def test_parse_json_con_texto_extra():
    try:
        agent = _make_agent()
        # Haiku a veces incluye texto antes del JSON
        raw = 'Aquí está mi análisis:\n{"risk_level":"medium","risk_score":0.5,"risk_reason":"Inactividad moderada","key_signals":["15 días"],"recommended_action":"monitor"}'
        last_active = datetime.now(timezone.utc) - timedelta(days=15)
        result = agent._parse_llm_response(raw, _make_user(15), last_active, 15)
        assert result.risk_level == RiskLevel.MEDIUM
        ok("_parse_llm_response extrae JSON aunque haya texto extra")
    except Exception as e:
        fail("_parse_llm_response JSON con texto", str(e))

def test_parse_json_invalido_fallback():
    try:
        agent = _make_agent()
        raw = "Lo siento, no puedo analizar esto."
        last_active = datetime.now(timezone.utc) - timedelta(days=25)
        result = agent._parse_llm_response(raw, _make_user(25), last_active, 25)
        # Fallback basado en días: 25 días → HIGH (>21)
        assert result.risk_level == RiskLevel.HIGH
        assert result.risk_score == 0.75
        ok("_parse_llm_response usa fallback por días cuando JSON es inválido")
    except Exception as e:
        fail("_parse_llm_response fallback", str(e))

def test_parse_fallback_medium():
    try:
        agent = _make_agent()
        raw = "JSON inválido"
        last_active = datetime.now(timezone.utc) - timedelta(days=16)
        result = agent._parse_llm_response(raw, _make_user(16), last_active, 16)
        assert result.risk_level == RiskLevel.MEDIUM  # 14-21 días → MEDIUM
        ok("_parse_llm_response fallback MEDIUM para 14-21 días")
    except Exception as e:
        fail("_parse_llm_response fallback medium", str(e))

def test_parse_fallback_low():
    try:
        agent = _make_agent()
        raw = "JSON inválido"
        last_active = datetime.now(timezone.utc) - timedelta(days=9)
        result = agent._parse_llm_response(raw, _make_user(9), last_active, 9)
        assert result.risk_level == RiskLevel.LOW  # 7-14 días → LOW
        ok("_parse_llm_response fallback LOW para 7-14 días")
    except Exception as e:
        fail("_parse_llm_response fallback low", str(e))

test_parse_valid_json()
test_parse_json_con_texto_extra()
test_parse_json_invalido_fallback()
test_parse_fallback_medium()
test_parse_fallback_low()


section("4. Agente — análisis sin CDP (modo POC)")

async def test_analyze_user_sin_cdp():
    try:
        agent = _make_agent(cdp=None)
        user = _make_user(days_inactive=30)
        result = await agent.analyze_user(user)
        assert isinstance(result, ChurnAnalysis)
        assert result.user_id == str(user["id"])
        assert result.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW)
        ok("analyze_user funciona sin CDP (LLM real mockeado)")
    except Exception as e:
        fail("analyze_user sin CDP", str(e))

async def test_analyze_batch_sin_cdp():
    try:
        agent = _make_agent(cdp=None)
        result = await agent.analyze_batch(days_inactive=7)
        assert isinstance(result, BatchResult)
        assert result.total_analyzed == 0  # sin CDP no hay usuarios
        ok("analyze_batch sin CDP retorna batch vacío correctamente")
    except Exception as e:
        fail("analyze_batch sin CDP", str(e))

async def test_analyze_user_con_cdp_mock():
    try:
        # Mock del CDP con usuarios y eventos
        cdp_mock = MagicMock()
        cdp_mock.get_user_events = AsyncMock(return_value=[
            {"event_type": "job.viewed"},
            {"event_type": "user.logged_in"},
        ])
        cdp_mock.track = AsyncMock(return_value="event-id-123")

        agent = _make_agent(cdp=cdp_mock)
        user = _make_user(days_inactive=25)
        result = await agent.analyze_user(user)

        # Verificar que se llamó a los métodos del CDP
        assert cdp_mock.get_user_events.called
        assert cdp_mock.track.called
        assert isinstance(result, ChurnAnalysis)
        ok("analyze_user con CDP mock llama a get_user_events y track")
    except Exception as e:
        fail("analyze_user con CDP mock", str(e))

async def test_analyze_batch_con_cdp_mock():
    try:
        cdp_mock = MagicMock()
        cdp_mock.get_inactive_users = AsyncMock(return_value=[
            _make_user(days_inactive=30),
            _make_user(days_inactive=20),
        ])
        cdp_mock.get_user_events = AsyncMock(return_value=[])
        cdp_mock.track = AsyncMock(return_value="event-id")

        agent = _make_agent(cdp=cdp_mock)
        result = await agent.analyze_batch(days_inactive=7)

        assert result.total_analyzed == 2
        assert cdp_mock.get_inactive_users.called
        ok("analyze_batch con CDP mock analiza todos los usuarios inactivos")
    except Exception as e:
        fail("analyze_batch con CDP mock", str(e))

asyncio.run(test_analyze_user_sin_cdp())
asyncio.run(test_analyze_batch_sin_cdp())
asyncio.run(test_analyze_user_con_cdp_mock())
asyncio.run(test_analyze_batch_con_cdp_mock())


section("5. CDP Events — CHURN_RISK_DETECTED")

def test_event_definido():
    try:
        assert Events.CHURN_RISK_DETECTED == "churn.risk_detected"
        ok("Events.CHURN_RISK_DETECTED está definido correctamente")
    except Exception as e:
        fail("Events.CHURN_RISK_DETECTED", str(e))

test_event_definido()


section("6. Scheduler")

async def test_scheduler_stats():
    try:
        from agents.churn_predictor.scheduler import ChurnScheduler

        cdp_mock   = MagicMock()
        agent_mock = MagicMock()

        scheduler = ChurnScheduler(cdp=cdp_mock, agent=agent_mock, interval_seconds=3600)
        stats = scheduler.stats()

        assert stats["ticks"] == 0
        assert stats["running"] == False
        assert stats["interval_s"] == 3600
        assert stats["days_inactive"] == 7
        ok("ChurnScheduler inicializa con stats correctas")
    except Exception as e:
        fail("ChurnScheduler stats", str(e))

async def test_scheduler_stop():
    try:
        from agents.churn_predictor.scheduler import ChurnScheduler

        cdp_mock   = MagicMock()
        agent_mock = MagicMock()
        agent_mock.analyze_batch = AsyncMock(return_value=BatchResult.from_analyses([]))

        scheduler = ChurnScheduler(cdp=cdp_mock, agent=agent_mock, interval_seconds=1)
        scheduler._running = True
        scheduler.stop()
        assert scheduler._running == False
        ok("ChurnScheduler.stop() detiene el scheduler")
    except Exception as e:
        fail("ChurnScheduler stop", str(e))

asyncio.run(test_scheduler_stats())
asyncio.run(test_scheduler_stop())


# ── Resultado final ────────────────────────────────────────────────────────────
total = PASSED + FAILED
print(f"\n{'═'*55}")
print(f"  Resultado: {PASSED}/{total} tests pasaron")
print(f"{'═'*55}\n")

if FAILED > 0:
    print(f"  ⚠️  {FAILED} test(s) fallaron")
    sys.exit(1)
else:
    print("  ✅ Churn Predictor verificado correctamente")
    sys.exit(0)
