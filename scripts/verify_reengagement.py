"""
Tests del Re-engagement Agent
──────────────────────────────────────────────────────────────────────────
Verifica el agente sin base de datos ni llamadas reales a Claude.

Ejecutar:
    python3 scripts/verify_reengagement.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Mocks ANTES de importar el proyecto ──────────────────────────────────────

def _mock_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

structlog_mod = _mock_module("structlog")
class _FakeLog:
    def __getattr__(self, name): return lambda *a, **kw: None
structlog_mod.get_logger = lambda *a, **kw: _FakeLog()
structlog_mod.configure  = lambda **kw: None

httpx_mod = _mock_module("httpx")
class _FakeHTTPResponse:
    def raise_for_status(self): pass
    def json(self):
        return {
            "content": json.dumps({
                "subject":       "Hola, hay vacantes para ti",
                "email_body":    "Hola,\n\nEchamos de menos tu visita...\n\nEl equipo de elempleo",
                "whatsapp_text": "Hola 👋 ¡Hay vacantes nuevas para ti!",
                "tone":          "empático",
            }),
            "usage": {"total_tokens": 400, "cost_usd": 0.010, "prompt_tokens": 300, "completion_tokens": 100},
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

httpx_mod.AsyncClient     = _FakeAsyncClient
httpx_mod.HTTPStatusError = Exception
httpx_mod.ConnectError    = Exception

_mock_module("asyncpg")

# ── Imports del proyecto ──────────────────────────────────────────────────────
from agents.reengagement.agent   import ReengagementAgent
from agents.reengagement.models  import (
    BatchSendResult,
    ProcessRequest,
    ProcessUserRequest,
    ReengagementMessage,
    SendResult,
)
from agents.reengagement.prompts import REENGAGEMENT_SYSTEM, reengagement_prompt
from cdp.events                  import Events

# ── Helpers ───────────────────────────────────────────────────────────────────
PASSED = FAILED = 0

def ok(name: str):
    global PASSED; PASSED += 1; print(f"  ✅ {name}")

def fail(name: str, reason: str):
    global FAILED; FAILED += 1; print(f"  ❌ {name}: {reason}")

def section(title: str):
    print(f"\n{'─'*55}\n  {title}\n{'─'*55}")

def _user(name="Ana García", city="Bogotá", title="Desarrolladora Backend"):
    return {
        "id":            "550e8400-e29b-41d4-a716-446655440001",
        "full_name":     name,
        "email":         "ana@example.com",
        "city":          city,
        "current_title": title,
        "experience_years": 3,
        "education_level":  "profesional",
        "skills":        ["Python", "Django"],
        "profile_completion": 60,
        "source":        "organic",
        "last_active_at": datetime.now(timezone.utc),
    }

def _churn(risk="high", days=25):
    return {
        "risk_level":    risk,
        "risk_reason":   "Sin actividad prolongada",
        "days_inactive": days,
        "key_signals":   ["25 días inactivo", "Sin postulaciones"],
    }

# ── Tests ─────────────────────────────────────────────────────────────────────

section("1. Modelos")

def test_reengagement_message():
    try:
        m = ReengagementMessage(
            subject="Hola, hay vacantes para ti",
            email_body="Cuerpo del email",
            whatsapp_text="Mensaje WhatsApp",
        )
        assert m.tone == "empático"
        ok("ReengagementMessage crea con defaults correctos")
    except Exception as e:
        fail("ReengagementMessage", str(e))

def test_send_result():
    try:
        r = SendResult(
            user_id="user-1", full_name="Ana", risk_level="high",
            channel="log", success=True, message_id="log-abc123",
        )
        assert r.success is True
        assert r.error is None
        ok("SendResult crea correctamente")
    except Exception as e:
        fail("SendResult", str(e))

def test_batch_send_result_empty():
    try:
        b = BatchSendResult.empty()
        assert b.total_processed == 0
        assert b.sent_ok == 0
        ok("BatchSendResult.empty() retorna valores correctos")
    except Exception as e:
        fail("BatchSendResult.empty", str(e))

def test_batch_send_result_counts():
    try:
        results = [
            SendResult(user_id="1", full_name="A", risk_level="high", channel="log", success=True),
            SendResult(user_id="2", full_name="B", risk_level="medium", channel="log", success=True),
            SendResult(user_id="3", full_name="C", risk_level="high", channel="log", success=False, error="timeout"),
        ]
        b = BatchSendResult(
            total_processed=3, sent_ok=2, sent_failed=1, results=results
        )
        assert b.sent_ok == 2
        assert b.sent_failed == 1
        ok("BatchSendResult cuenta ok/failed correctamente")
    except Exception as e:
        fail("BatchSendResult counts", str(e))

test_reengagement_message()
test_send_result()
test_batch_send_result_empty()
test_batch_send_result_counts()


section("2. Prompts")

def test_system_prompt():
    try:
        assert "subject" in REENGAGEMENT_SYSTEM
        assert "email_body" in REENGAGEMENT_SYSTEM
        assert "whatsapp_text" in REENGAGEMENT_SYSTEM
        assert "español" in REENGAGEMENT_SYSTEM.lower() or "colombiano" in REENGAGEMENT_SYSTEM.lower()
        ok("REENGAGEMENT_SYSTEM contiene campos JSON requeridos")
    except Exception as e:
        fail("REENGAGEMENT_SYSTEM", str(e))

def test_prompt_builder_high_risk():
    try:
        msg = reengagement_prompt(_user(), _churn(risk="high", days=30))
        assert "Ana García" in msg
        assert "30" in msg
        assert "Bogotá" in msg
        assert "urgente" in msg.lower() or "empático" in msg.lower() or "inactivo" in msg.lower()
        ok("reengagement_prompt HIGH incluye datos y tono")
    except Exception as e:
        fail("reengagement_prompt high", str(e))

def test_prompt_builder_medium_risk():
    try:
        msg = reengagement_prompt(_user(), _churn(risk="medium", days=14))
        assert "14" in msg
        assert "motivador" in msg.lower() or "oportunidades" in msg.lower()
        ok("reengagement_prompt MEDIUM usa tono motivador")
    except Exception as e:
        fail("reengagement_prompt medium", str(e))

def test_prompt_sin_skills():
    try:
        user = _user()
        user["skills"] = []
        msg = reengagement_prompt(user, _churn())
        assert "No especificadas" in msg
        ok("reengagement_prompt maneja skills vacías")
    except Exception as e:
        fail("reengagement_prompt sin skills", str(e))

test_system_prompt()
test_prompt_builder_high_risk()
test_prompt_builder_medium_risk()
test_prompt_sin_skills()


section("3. Agente — parse de mensajes")

def test_parse_valid_json():
    try:
        agent = ReengagementAgent()
        raw = json.dumps({
            "subject":       "Ana, hay vacantes para ti 💼",
            "email_body":    "Hola Ana,\n\nTenemos nuevas oportunidades...",
            "whatsapp_text": "Hola Ana 👋 ¡Hay vacantes nuevas!",
            "tone":          "motivador",
        })
        msg = agent._parse_message(raw, _user(), _churn())
        assert msg.subject == "Ana, hay vacantes para ti 💼"
        assert msg.tone == "motivador"
        assert "Ana" in msg.email_body
        ok("_parse_message parsea JSON válido")
    except Exception as e:
        fail("_parse_message JSON válido", str(e))

def test_parse_json_con_texto_extra():
    try:
        agent = ReengagementAgent()
        raw = 'Aquí está el mensaje:\n{"subject":"Hola","email_body":"Body","whatsapp_text":"WA","tone":"empático"}'
        msg = agent._parse_message(raw, _user(), _churn())
        assert msg.subject == "Hola"
        ok("_parse_message extrae JSON aunque haya texto extra")
    except Exception as e:
        fail("_parse_message texto extra", str(e))

def test_parse_json_invalido_fallback():
    try:
        agent = ReengagementAgent()
        raw = "No puedo generar ese mensaje."
        msg = agent._parse_message(raw, _user(), _churn())
        # Fallback debe generar un mensaje coherente
        assert "Ana García" in msg.subject or "Ana" in msg.email_body
        assert len(msg.email_body) > 50
        assert len(msg.whatsapp_text) > 10
        ok("_parse_message genera fallback coherente cuando JSON es inválido")
    except Exception as e:
        fail("_parse_message fallback", str(e))

def test_parse_fallback_sin_nombre():
    try:
        agent = ReengagementAgent()
        user  = _user(name="")
        msg   = agent._parse_message("json inválido", user, _churn())
        assert len(msg.email_body) > 0
        ok("_parse_message fallback funciona sin nombre de usuario")
    except Exception as e:
        fail("_parse_message fallback sin nombre", str(e))

test_parse_valid_json()
test_parse_json_con_texto_extra()
test_parse_json_invalido_fallback()
test_parse_fallback_sin_nombre()


section("4. Agente — process_user sin pool/CDP")

async def test_process_user_sin_pool():
    try:
        agent = ReengagementAgent(cdp=None, pool=None)
        result = await agent.process_user("user-123", _churn())
        # Sin pool no puede obtener perfil → retorna SendResult con error
        assert result.user_id == "user-123"
        assert result.success is False
        assert result.error is not None
        ok("process_user sin pool retorna error claro")
    except Exception as e:
        fail("process_user sin pool", str(e))

async def test_process_user_con_pool_mock():
    try:
        # Mock del pool para devolver un usuario
        conn_mock = MagicMock()
        conn_mock.fetchrow = AsyncMock(return_value=_user())
        conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
        conn_mock.__aexit__  = AsyncMock(return_value=None)

        pool_mock = MagicMock()
        pool_mock.acquire = MagicMock(return_value=conn_mock)

        cdp_mock       = MagicMock()
        cdp_mock.track = AsyncMock(return_value="event-id")

        agent  = ReengagementAgent(cdp=cdp_mock, pool=pool_mock)
        result = await agent.process_user("550e8400-e29b-41d4-a716-446655440001", _churn())

        assert result.success is True
        assert result.full_name == "Ana García"
        assert result.channel in ("log", "email", "whatsapp")
        assert cdp_mock.track.called
        ok("process_user con pool mock genera y envía mensaje")
    except Exception as e:
        fail("process_user con pool mock", str(e))

async def test_process_pending_sin_pool():
    try:
        agent  = ReengagementAgent(cdp=None, pool=None)
        result = await agent.process_pending()
        assert result.total_processed == 0
        ok("process_pending sin pool retorna batch vacío")
    except Exception as e:
        fail("process_pending sin pool", str(e))

asyncio.run(test_process_user_sin_pool())
asyncio.run(test_process_user_con_pool_mock())
asyncio.run(test_process_pending_sin_pool())


section("5. CDP Events — REENGAGEMENT_SENT")

def test_event_definido():
    try:
        assert Events.REENGAGEMENT_SENT == "reengagement.message_sent"
        ok("Events.REENGAGEMENT_SENT está definido correctamente")
    except Exception as e:
        fail("Events.REENGAGEMENT_SENT", str(e))

test_event_definido()


section("6. Scheduler")

async def test_scheduler_stats():
    try:
        from agents.reengagement.scheduler import ReengagementScheduler
        cdp_mock   = MagicMock()
        pool_mock  = MagicMock()
        agent_mock = MagicMock()

        s = ReengagementScheduler(cdp=cdp_mock, pool=pool_mock, agent=agent_mock)
        stats = s.stats()
        assert stats["ticks"] == 0
        assert stats["running"] is False
        assert stats["interval_s"] == 1800
        ok("ReengagementScheduler inicializa con stats correctas")
    except Exception as e:
        fail("ReengagementScheduler stats", str(e))

async def test_scheduler_stop():
    try:
        from agents.reengagement.scheduler import ReengagementScheduler
        s = ReengagementScheduler(cdp=MagicMock(), pool=MagicMock(), agent=MagicMock())
        s._running = True
        s.stop()
        assert s._running is False
        ok("ReengagementScheduler.stop() detiene correctamente")
    except Exception as e:
        fail("ReengagementScheduler stop", str(e))

asyncio.run(test_scheduler_stats())
asyncio.run(test_scheduler_stop())


# ── Resultado ─────────────────────────────────────────────────────────────────
total = PASSED + FAILED
print(f"\n{'═'*55}")
print(f"  Resultado: {PASSED}/{total} tests pasaron")
print(f"{'═'*55}\n")

if FAILED > 0:
    print(f"  ⚠️  {FAILED} test(s) fallaron")
    sys.exit(1)
else:
    print("  ✅ Re-engagement Agent verificado correctamente")
    sys.exit(0)
