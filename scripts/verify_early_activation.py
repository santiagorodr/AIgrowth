"""
Verificación end-to-end del Early Activation Agent.

Valida sin depender de Docker, PostgreSQL, Redis ni APIs externas:
  ✓ Todos los módulos importan correctamente
  ✓ Los modelos Pydantic son válidos
  ✓ Los prompts generan texto no vacío
  ✓ Los canales responden (LogChannel siempre éxito)
  ✓ El agente ejecuta los 5 pasos en memoria sin errores
  ✓ El JSON del LLM se parsea correctamente (con y sin markdown)
  ✓ Las condiciones se evalúan correctamente (sin DB → siempre True)
  ✓ El scheduler crea su instancia y sus stats son coherentes
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Agregar raíz del proyecto al path ─────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Mock de módulos pesados ANTES de cualquier import del proyecto ────────────
def _mock_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# structlog mock
structlog_mod = _mock_module("structlog")
structlog_mod.get_logger = lambda *a, **kw: _FakeLog()

class _FakeLog:
    def __getattr__(self, name):
        return lambda *a, **kw: None

# httpx mock (canales usan httpx)
httpx_mod = _mock_module("httpx")

class _FakeResp:
    status_code = 200
    text = '{"message_ids": ["mock-id-001"]}'
    def raise_for_status(self): pass
    def json(self): return {"message_ids": ["mock-id-001"]}

class _FakeAsyncClient:
    def __init__(self, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, *a, **kw): return _FakeResp()

httpx_mod.AsyncClient = _FakeAsyncClient
httpx_mod.HTTPStatusError = Exception
httpx_mod.ConnectError = Exception

# asyncpg mock
asyncpg_mod = _mock_module("asyncpg")

# qdrant_client mock
qdrant_mod = _mock_module("qdrant_client")
qdrant_mod.models = types.ModuleType("qdrant_client.models")
sys.modules["qdrant_client.models"] = qdrant_mod.models

# sentence_transformers mock
st_mod = _mock_module("sentence_transformers")
class _FakeST:
    def __init__(self, *a, **kw): pass
    def encode(self, texts, *a, **kw):
        import random
        return [[random.random() for _ in range(384)] for _ in (texts if isinstance(texts, list) else [texts])]
st_mod.SentenceTransformer = _FakeST

# ── Colores ANSI ──────────────────────────────────────────────────────────────
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

passed = 0
failed = 0

def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    print(f"  {icon}  {label}" + (f"  {YELLOW}({detail}){RESET}" if detail else ""))
    if ok:
        passed += 1
    else:
        failed += 1

def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─'*55}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*55}{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# 1. IMPORTS
# ══════════════════════════════════════════════════════════════════════════════
section("1. Imports")

try:
    from agents.early_activation.models import (
        ActivationEvent, Channel, ChannelResult,
        GeneratedMessage, SequenceStatus, SequenceStepConfig, StepKey,
    )
    check("models.py importa", True)
except Exception as e:
    check("models.py importa", False, str(e))

try:
    from agents.early_activation.sequences import SEQUENCE, SEQUENCE_BY_KEY
    check("sequences.py importa", True)
except Exception as e:
    check("sequences.py importa", False, str(e))

try:
    from agents.early_activation.prompts import (
        ACTIVATION_SYSTEM, welcome_prompt, cv_tip_prompt,
        employer_signal_prompt, first_apply_nudge_prompt, reactivation_check_prompt,
    )
    check("prompts.py importa", True)
except Exception as e:
    check("prompts.py importa", False, str(e))

try:
    from agents.early_activation.channels import (
        LogChannel, EmailChannel, WhatsAppChannel, get_channel,
    )
    check("channels.py importa", True)
except Exception as e:
    check("channels.py importa", False, str(e))

try:
    from agents.early_activation.agent import EarlyActivationAgent
    check("agent.py importa", True)
except Exception as e:
    check("agent.py importa", False, str(e))

try:
    from agents.early_activation.scheduler import ActivationScheduler
    check("scheduler.py importa", True)
except Exception as e:
    check("scheduler.py importa", False, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 2. MODELOS PYDANTIC
# ══════════════════════════════════════════════════════════════════════════════
section("2. Modelos Pydantic")

try:
    event = ActivationEvent(
        user_id=str(uuid.uuid4()),
        full_name="María García",
        email="maria@test.com",
        phone="+573001234567",
        source="whatsapp",
        city="Medellín",
        current_title="Desarrolladora Frontend",
        skills=["React", "TypeScript", "CSS"],
        experience_years=3,
        registered_at=datetime.now(timezone.utc),
    )
    check("ActivationEvent construye", True)
    check("ActivationEvent.full_name correcto", event.full_name == "María García")
    check("ActivationEvent.skills es lista", isinstance(event.skills, list))
except Exception as e:
    check("ActivationEvent construye", False, str(e))

try:
    result = ChannelResult(success=True, channel=Channel.LOG, message_id="test-001")
    check("ChannelResult construye", True)
    check("ChannelResult.success=True", result.success)
except Exception as e:
    check("ChannelResult construye", False, str(e))

try:
    check("SEQUENCE tiene 5 pasos", len(SEQUENCE) == 5)
    check("SEQUENCE_BY_KEY tiene 5 entries", len(SEQUENCE_BY_KEY) == 5)
    check("Paso 0 es WELCOME con delay=0", SEQUENCE[0].key == StepKey.WELCOME and SEQUENCE[0].delay_hours == 0)
    check("Paso 1 es CV_TIP con delay=2",  SEQUENCE[1].key == StepKey.CV_TIP  and SEQUENCE[1].delay_hours == 2)
    check("Paso 2 delay=24h", SEQUENCE[2].delay_hours == 24)
    check("Paso 3 condición no_application", SEQUENCE[3].condition == "no_application")
    check("Paso 4 condición inactive",       SEQUENCE[4].condition == "inactive")
except Exception as e:
    check("Validación de SEQUENCE", False, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 3. PROMPTS
# ══════════════════════════════════════════════════════════════════════════════
section("3. Prompts")

user_dict = event.model_dump()
mock_jobs = [
    {"title": "Frontend Developer", "company": "Rappi", "city": "Medellín", "modality": "híbrido", "match_score": 0.91},
    {"title": "React Engineer",     "company": "Nu",    "city": "Bogotá",   "modality": "remoto",  "match_score": 0.85},
]

try:
    p = welcome_prompt(user_dict, mock_jobs)
    check("welcome_prompt genera texto",   len(p) > 100)
    check("welcome_prompt menciona nombre", "María" in p)
    check("welcome_prompt menciona vacantes", "Frontend Developer" in p or "React" in p)
except Exception as e:
    check("welcome_prompt", False, str(e))

try:
    p = cv_tip_prompt(user_dict, 35)
    check("cv_tip_prompt (35%)",  len(p) > 50)
    p2 = cv_tip_prompt(user_dict, 65)
    check("cv_tip_prompt (65%)",  len(p2) > 50)
    p3 = cv_tip_prompt(user_dict, 85)
    check("cv_tip_prompt (85%)",  len(p3) > 50)
except Exception as e:
    check("cv_tip_prompt", False, str(e))

try:
    demand = {"companies_hiring": 15, "new_jobs_this_week": 42, "avg_salary_range": "3M-5M COP"}
    p = employer_signal_prompt(user_dict, demand, mock_jobs)
    check("employer_signal_prompt genera texto", len(p) > 100)
    check("employer_signal_prompt menciona empresas", "15" in p)
except Exception as e:
    check("employer_signal_prompt", False, str(e))

try:
    p = first_apply_nudge_prompt(user_dict, mock_jobs)
    check("first_apply_nudge_prompt genera texto", len(p) > 50)
except Exception as e:
    check("first_apply_nudge_prompt", False, str(e))

try:
    p = reactivation_check_prompt(user_dict)
    check("reactivation_check_prompt genera texto", len(p) > 50)
    check("ACTIVATION_SYSTEM no vacío", len(ACTIVATION_SYSTEM) > 100)
except Exception as e:
    check("reactivation_check_prompt", False, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 4. CANALES
# ══════════════════════════════════════════════════════════════════════════════
section("4. Canales")

async def test_channels():
    log_ch = LogChannel()
    check("LogChannel.is_configured()", log_ch.is_configured())

    r = await log_ch.send(to="test@test.com", subject="Prueba", body="Hola mundo")
    check("LogChannel.send() éxito",    r.success)
    check("LogChannel retorna message_id", r.message_id is not None)

    email_ch = EmailChannel()
    check("EmailChannel sin credenciales → no configurado", not email_ch.is_configured())
    # Sin credenciales debe delegar a LogChannel
    r2 = await email_ch.send(to="test@test.com", subject="Test", body="Cuerpo")
    check("EmailChannel fallback a LogChannel", r2.success and r2.channel == Channel.LOG)

    wa_ch = WhatsAppChannel()
    check("WhatsAppChannel sin credenciales → no configurado", not wa_ch.is_configured())
    r3 = await wa_ch.send(to="+573001234567", body="Hola!")
    check("WhatsAppChannel fallback a LogChannel", r3.success and r3.channel == Channel.LOG)

    # get_channel factory
    ch = get_channel(Channel.EMAIL)
    check("get_channel(EMAIL) → LogChannel (sin credenciales)", ch.__class__.__name__ == "LogChannel")
    ch2 = get_channel(Channel.LOG)
    check("get_channel(LOG) → LogChannel", ch2.__class__.__name__ == "LogChannel")

asyncio.run(test_channels())


# ══════════════════════════════════════════════════════════════════════════════
# 5. AGENTE — _parse_generated
# ══════════════════════════════════════════════════════════════════════════════
section("5. Parsing de respuestas LLM")

agent = EarlyActivationAgent()  # sin DB

# JSON limpio
raw_clean = json.dumps({
    "subject": "¡Bienvenido a elempleo! 🚀",
    "email_body": "Hola María, encontramos 3 vacantes para ti...",
    "whatsapp_text": "¡Hola María! Hay vacantes esperándote 👋",
})
msg = agent._parse_generated(raw_clean, StepKey.WELCOME)
check("Parseo JSON limpio — subject", "Bienvenido" in msg.subject)
check("Parseo JSON limpio — email_body", len(msg.body) > 0)
check("Parseo JSON limpio — whatsapp_text", len(msg.whatsapp_text) > 0)

# JSON con bloque markdown
raw_markdown = f"```json\n{raw_clean}\n```"
msg2 = agent._parse_generated(raw_markdown, StepKey.WELCOME)
check("Parseo JSON con ```json``` — subject", "Bienvenido" in msg2.subject)

# JSON con texto previo
raw_prefixed = f"Aquí está el mensaje solicitado:\n\n{raw_clean}"
msg3 = agent._parse_generated(raw_prefixed, StepKey.WELCOME)
check("Parseo JSON con texto previo — subject", len(msg3.subject) > 0)

# JSON inválido → fallback a subject del step
raw_invalid = "Lo siento, no puedo generar este contenido."
msg4 = agent._parse_generated(raw_invalid, StepKey.WELCOME)
check("Parseo JSON inválido → fallback subject", len(msg4.subject) > 0)


# ══════════════════════════════════════════════════════════════════════════════
# 6. AGENTE — condiciones sin DB
# ══════════════════════════════════════════════════════════════════════════════
section("6. Condiciones (modo sin DB)")

async def test_conditions():
    agent_nd = EarlyActivationAgent()  # sin pool
    ok1 = await agent_nd._check_condition("always", "user-1")
    check("Condición 'always' → True", ok1)
    ok2 = await agent_nd._check_condition("no_application", "user-1")
    check("Condición 'no_application' sin DB → True (asumir)", ok2)
    ok3 = await agent_nd._check_condition("inactive", "user-1")
    check("Condición 'inactive' sin DB → True (asumir)", ok3)

asyncio.run(test_conditions())


# ══════════════════════════════════════════════════════════════════════════════
# 7. AGENTE — ejecución de paso en memoria (mock LLM)
# ══════════════════════════════════════════════════════════════════════════════
section("7. Ejecución de paso en memoria (mock LLM)")

# Monkey-patch del método llm para no necesitar el Gateway
_LLM_RESPONSE = json.dumps({
    "subject": "¡Bienvenido a elempleo! 🚀",
    "email_body": "Hola María, encontramos 3 vacantes para ti en Medellín.",
    "whatsapp_text": "¡Hola María! Hay 3 vacantes esperándote 👋",
})

async def mock_llm(self, task_type, system, user_message, **kw):
    return _LLM_RESPONSE

# También mock del Job Match Agent para que _get_top_jobs no falle
class _FakeJMAgent:
    async def run(self, request):
        class R:
            matched_jobs = []
        return R()
    async def close(self): pass

async def test_step_in_memory():
    from agents.early_activation import agent as agent_module
    original_llm = EarlyActivationAgent.llm

    EarlyActivationAgent.llm = mock_llm

    # También patch _get_top_jobs para no necesitar Qdrant
    async def mock_jobs(self, ev):
        return [
            {"title": "Frontend Dev", "company": "Rappi", "city": "Medellín", "modality": "híbrido", "match_score": 0.9},
        ]
    EarlyActivationAgent._get_top_jobs = mock_jobs

    agent_mem = EarlyActivationAgent()

    for step_key in StepKey:
        try:
            result = await agent_mem._run_step_in_memory(step_key, event)
            check(f"Paso {step_key.value} ejecuta sin error", True)
            check(f"Paso {step_key.value} → success", result.success)
        except Exception as exc:
            check(f"Paso {step_key.value} ejecuta sin error", False, str(exc))

    EarlyActivationAgent.llm = original_llm

asyncio.run(test_step_in_memory())


# ══════════════════════════════════════════════════════════════════════════════
# 8. SCHEDULER — instanciación y stats
# ══════════════════════════════════════════════════════════════════════════════
section("8. Scheduler")

class _MockAgent:
    async def execute_step(self, row, event=None):
        return ChannelResult(success=True, channel=Channel.LOG, message_id="mock-01")

scheduler = ActivationScheduler(pool=None, agent=_MockAgent(), interval_seconds=30)
check("ActivationScheduler instancia", scheduler is not None)
check("Scheduler interval=30s",        scheduler._interval == 30)
stats = scheduler.stats()
check("stats() retorna dict",          isinstance(stats, dict))
check("stats.ticks=0 inicial",         stats["ticks"] == 0)
check("stats.running=False inicial",   not stats["running"])


# ══════════════════════════════════════════════════════════════════════════════
# 9. API — router se registra
# ══════════════════════════════════════════════════════════════════════════════
section("9. API router")

try:
    import importlib
    fastapi_spec = importlib.util.find_spec("fastapi")
    if fastapi_spec is None:
        check("API router (fastapi disponible)", False, "fastapi no instalado — instala con: pip install fastapi")
    else:
        from agents.early_activation.api import router
        routes = [r.path for r in router.routes]
        check("Router importa", True)
        check("/trigger existe",          any("/trigger" in r for r in routes))
        check("/status/{user_id} existe", any("status" in r for r in routes))
        check("/step existe",             any("/step" in r for r in routes))
except Exception as e:
    check("API router importa", False, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# RESULTADO FINAL
# ══════════════════════════════════════════════════════════════════════════════
total = passed + failed
print(f"\n{'─'*55}")
print(f"{BOLD}  Resultado: {GREEN}{passed} pasaron{RESET}{BOLD}  /  {RED}{failed} fallaron{RESET}{BOLD}  /  {total} total{RESET}")
print(f"{'─'*55}\n")

if failed == 0:
    print(f"{GREEN}{BOLD}  ✅  Early Activation Agent verificado — todo en orden{RESET}\n")
    sys.exit(0)
else:
    print(f"{RED}{BOLD}  ❌  Hay {failed} test(s) fallando — revisar arriba{RESET}\n")
    sys.exit(1)
