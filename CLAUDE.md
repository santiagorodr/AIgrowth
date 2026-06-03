# CLAUDE.md — Elempleo AI Growth Engine

## Qué es este proyecto

Sistema multi-agente de IA para adquisición, activación y retención de candidatos en **elempleo.com**, sin equipo comercial. Los agentes corren de forma autónoma usando Claude (Anthropic) como motor de razonamiento y generación de mensajes personalizados.

**Objetivo de negocio:** aumentar la tasa de activación y retención de candidatos en el marketplace mediante comunicaciones personalizadas y matching semántico, a un costo marginal por usuario de ~$0.01–0.02 USD.

---

## Arquitectura del stack

```
Mac M1 8GB (agentes Python — sin Docker)
┌─────────────────────────────────────────────────────────────┐
│                    AGENTES (Python)                         │
│   JobMatchAgent  EarlyActivationAgent  ChurnPredictor ...   │
│              └────────────┬────────────────┘                │
│                     BaseAgent                               │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP
┌────────────────────────────▼────────────────────────────────┐
│         LLM GATEWAY — Railway (24/7, cloud)                 │
│  elempleo-gateway-production.up.railway.app                 │
│   Routing Haiku/Sonnet · Retries · Cost tracking            │
└────────┬──────────────────────────────────────┬────────────┘
         │ asyncpg                               │ Anthropic SDK
         │                               ┌──────▼──────────┐
┌────────▼──────────────┐                │  Claude API     │
│  Supabase (cloud)     │                │  Haiku / Sonnet │
│  PostgreSQL           │                └─────────────────┘
│  CDP · Logs · Sched.  │
└───────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  Qdrant Cloud (AWS us-east-1)                               │
│  Vector DB · elempleo_jobs + elempleo_users                 │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  sentence-transformers (local, sin API key)                 │
│  paraphrase-multilingual-MiniLM-L12-v2 · ~470MB · gratis   │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  Mailtrap Sandbox (email testing)                           │
│  Canal de email real verificado — fallback a LogChannel     │
└─────────────────────────────────────────────────────────────┘
```

**Event Bus eliminado:** los agentes de Fase 2 usan polling sobre PostgreSQL (Supabase) en lugar de Redis pub/sub. Más simple, sin servicios adicionales, 0 contenedores Docker.

**LLM Gateway en Railway:** corre 24/7 sin depender de la Mac. Deploy automático desde GitHub main. Ver `railway.toml` en la raíz del proyecto.

---

## Estructura de carpetas

```
elempleo-ai-growth/
├── agents/
│   ├── base.py                  # BaseAgent — clase base de todos los agentes
│   ├── server.py                # FastAPI app principal con todos los routers
│   ├── job_match/
│   │   ├── agent.py             # JobMatchAgent — matching semántico + reranking
│   │   ├── api.py               # Router FastAPI: POST /agents/job-match/search
│   │   ├── demo.py              # Demo CLI interactiva
│   │   ├── models.py            # Pydantic models
│   │   └── prompts.py           # System prompt + template de reranking
│   ├── early_activation/
│   │   ├── agent.py             # EarlyActivationAgent — secuencia 72h
│   │   ├── api.py               # Router FastAPI: trigger / status / step
│   │   ├── channels.py          # Adaptadores Email, WhatsApp, LogChannel (fallback)
│   │   ├── demo.py              # Demo CLI con --user N, --custom, --no-llm, --delay
│   │   ├── models.py            # Pydantic models: SequenceStep, OnboardingSequence
│   │   ├── prompts.py           # Prompts personalizados por paso de la secuencia
│   │   ├── scheduler.py         # Async polling scheduler (asyncio, SELECT FOR UPDATE)
│   │   └── sequences.py         # Definición de los 5 pasos: welcome→cv_tip→...→reactivation
│   ├── churn_predictor/
│   │   ├── agent.py             # ChurnPredictorAgent — clasifica riesgo HIGH/MEDIUM/LOW
│   │   ├── api.py               # Router FastAPI
│   │   ├── demo.py              # Demo CLI
│   │   ├── models.py            # Pydantic models
│   │   ├── prompts.py           # System prompts
│   │   └── scheduler.py         # Polling horario sobre usuarios inactivos >7 días
│   ├── reengagement/
│   │   ├── agent.py             # ReengagementAgent — mensajes personalizados de reactivación
│   │   ├── api.py               # Router FastAPI
│   │   ├── demo.py              # Demo CLI
│   │   ├── models.py            # Pydantic models
│   │   ├── prompts.py           # System prompts
│   │   └── scheduler.py         # Polling 30min, deduplicación 72h
│   ├── matching_notifier/
│   │   ├── agent.py             # MatchingNotifierAgent — búsqueda inversa + alerta candidatos
│   │   ├── api.py               # Router FastAPI
│   │   ├── demo.py              # Demo CLI
│   │   ├── models.py            # Pydantic models
│   │   ├── prompts.py           # System prompts (Haiku)
│   │   └── scheduler.py         # Polling 6h sobre vacantes nuevas, dedup 72h
│   ├── profile_optimizer/
│   │   ├── agent.py             # ProfileOptimizerAgent — gap analysis perfil vs vacantes
│   │   ├── api.py               # Router FastAPI
│   │   ├── demo.py              # Demo CLI
│   │   ├── models.py            # Pydantic models (SuggestionPriority, ProfileSuggestion)
│   │   ├── prompts.py           # System prompts (Sonnet)
│   │   └── scheduler.py         # Polling diario sobre perfiles con completion < 70%
│   └── employer_signal/
│       ├── agent.py             # EmployerSignalAgent — detecta visitas + notifica candidatos
│       ├── api.py               # Router FastAPI: /process, /simulate, /notify/{user_id}
│       ├── demo.py              # Demo CLI con simulador integrado
│       ├── models.py            # Pydantic models (EmployerView, SignalResult)
│       ├── prompts.py           # System prompts (Haiku — mensajes motivadores)
│       └── scheduler.py         # Polling cada 15 min sobre eventos employer.viewed_profile
├── cdp/
│   ├── events.py                # CDPClient + Events (catálogo de event_types) — SIN Redis
│   └── schema.sql               # Schema PostgreSQL: users, jobs, events, agent_logs, sequences
├── event_bus/
│   └── bus.py                   # STUB no-op — Redis eliminado, mantiene interfaz para imports
├── gateway/
│   ├── main.py                  # FastAPI app del LLM Gateway
│   ├── claude_client.py         # Wrapper Anthropic SDK con routing y cost tracking
│   └── models.py                # CompletionRequest, CompletionResponse, TaskType
├── vector_db/
│   ├── embedder.py              # JobEmbedder: index_jobs, search_jobs, recommend_for_user
│   └── setup.py                 # create_collections() para Qdrant Cloud
├── scripts/
│   ├── load_data.py                    # Carga mock_jobs.json y mock_users.json al stack
│   ├── health_check.py                 # Verifica los 4 servicios del stack (sin Redis)
│   ├── verify_agent.py                 # Tests del Job Match Agent
│   ├── verify_early_activation.py      # Tests del Early Activation Agent (66 tests)
│   ├── verify_churn_predictor.py       # Tests del Churn Predictor (20 tests)
│   ├── verify_reengagement.py          # Tests del Re-engagement Agent (18 tests)
│   ├── verify_matching_notifier.py     # Tests del Matching Notifier (15 tests)
│   ├── verify_profile_optimizer.py     # Tests del Profile Optimizer (16 tests)
│   └── verify_employer_signal.py       # Tests del Employer Signal Agent (16 tests)
├── data/
│   ├── mock_jobs.json           # 25 vacantes colombianas realistas
│   └── mock_users.json          # 20 perfiles de candidatos
├── docker-compose.yml.bak       # ARCHIVADO — ya no se usa (migrado a cloud)
├── railway.toml                 # Config de deploy Railway (build context, healthcheck)
├── Makefile                     # Comandos principales (ver sección abajo)
├── requirements.txt             # Dependencias Python
└── .env                         # Variables de entorno (ANTHROPIC_API_KEY, etc.)
```

---

## Comandos principales

```bash
# Operación diaria (una sola terminal necesaria — Gateway ya corre en Railway)
make test              # Health check: Supabase + Qdrant Cloud + Gateway + Embeddings
make gateway-dev       # Levanta LLM Gateway LOCAL en :8000 (solo para desarrollo)

# Datos (si Qdrant Cloud queda vacío tras migración o reset)
make setup-cloud       # Re-crea colecciones + re-indexa vacantes en Qdrant Cloud
make load-data         # Carga vacantes y usuarios mock a Supabase + Qdrant
make load-jobs         # Solo vacantes
make load-users        # Solo usuarios

# Demos (requieren Gateway local o apuntar a GATEWAY_URL_PROD)
make demo-job-match          # Demo interactiva del Job Match Agent
make demo-activation         # Demo Early Activation Agent (con Claude)
make demo-activation-offline # Demo sin llamar a Claude (más rápido)
make demo-employer           # Demo Employer Signal Agent (simula señales + notifica)
make demo-employer-offline   # Demo Employer Signal sin Claude

# Verificación
make verify-agent            # Tests Job Match Agent
make verify-activation       # Tests Early Activation Agent
make verify-churn            # Tests Churn Predictor (20 tests)
make verify-reengagement     # Tests Re-engagement Agent (18 tests)
make verify-matching         # Tests Matching Notifier (15 tests)
make verify-profile          # Tests Profile Optimizer (16 tests)
make verify-employer         # Tests Employer Signal Agent (16 tests)

# Railway
railway logs             # Ver logs del Gateway en producción
railway status           # Estado del servicio en Railway
railway up               # Re-deployar manualmente (normalmente auto desde GitHub)
```

---

## Variables de entorno (.env)

```env
ANTHROPIC_API_KEY=sk-ant-...

# Supabase (session pooler — formato con proyecto en el usuario)
POSTGRES_URL=postgresql://postgres.[PROJECT_REF]:[PASSWORD]@aws-1-us-east-2.pooler.supabase.com:5432/postgres

# Qdrant Cloud (AWS us-east-1)
QDRANT_URL=https://[CLUSTER_ID].us-east-1-1.aws.cloud.qdrant.io
QDRANT_API_KEY=[JWT_API_KEY]
QDRANT_COLLECTION=elempleo_jobs

# LLM Gateway
GATEWAY_URL=http://localhost:8000                                      # desarrollo local
GATEWAY_URL_PROD=https://elempleo-gateway-production.up.railway.app   # producción (Railway)

# Email Sandbox (Mailtrap)
MAILTRAP_TOKEN=[TOKEN]        # Obtener en mailtrap.io → Sandboxes → API Tokens
MAILTRAP_INBOX_ID=[INBOX_ID]  # Número en la URL: mailtrap.io/sandboxes/[ID]/...
```

**Importante:** La URL de Supabase usa el formato `postgres.[project_ref]` como usuario (session pooler). La URL directa `db.[ref].supabase.co` ya no resuelve DNS en proyectos nuevos.

**Railway env vars:** `ANTHROPIC_API_KEY` y `POSTGRES_URL` están configuradas en Railway para el Gateway en producción. Nunca se commitean al repo.

---

## Cómo agregar un nuevo agente

1. Crear carpeta `agents/nuevo_agente/` con `__init__.py`
2. Crear `agent.py` heredando de `BaseAgent`:

```python
from agents.base import BaseAgent
from cdp.events import Events

class NuevoAgente(BaseAgent):
    AGENT_ID = "nuevo_agente"   # snake_case, único en el sistema

    async def run(self, user: dict) -> dict:
        await self.log_run("started", user_id=user["id"])

        # Haiku para clasificación/extracción (barato)
        resultado = await self.llm(
            task_type="classification",
            system="Eres un experto en...",
            user_message=f"Clasifica: {user}",
            max_tokens=200,
            temperature=0.0,
        )

        await self.track(Events.AGENT_COMPLETED, user_id=user["id"],
                         properties={"resultado": resultado})
        await self.log_run("completed", user_id=user["id"])
        return {"content": resultado}
```

3. Crear `api.py` con el router FastAPI
4. Registrar el router en `agents/server.py`
5. Crear `demo.py` para pruebas locales
6. Crear `scripts/verify_nuevo_agente.py` y agregar targets al Makefile

---

## Modelo de costos (Claude API)

| Modelo | Uso | Precio input | Precio output |
|--------|-----|-------------|---------------|
| `claude-haiku-4-5-20251001` | Clasificación, extracción | $0.25/M tokens | $1.25/M tokens |
| `claude-sonnet-4-6` | Generación, razonamiento | $3.00/M tokens | $15.00/M tokens |

**Costo real observado:**
- Job Match reranking 16 candidatos: ~$0.023 USD
- Early Activation 1 mensaje: ~$0.010 USD
- Secuencia 72h completa (5 pasos): ~$0.050 USD por usuario
- Clasificación simple Haiku: ~$0.0003 USD
- Churn Predictor por usuario: ~$0.0004 USD (Haiku)
- Matching Notifier por notificación: ~$0.0002 USD (Haiku)
- Profile Optimizer por reporte: ~$0.015 USD (Sonnet)

El routing Haiku/Sonnet es automático en el LLM Gateway según `task_type`.

---

## Estado del proyecto

### Fase 1 — Completada ✅ + Migrada a cloud ✅

| Componente | Estado | Notas |
|---|---|---|
| Stack infraestructura | ✅ cloud | Supabase (PostgreSQL) + Qdrant Cloud + LLM Gateway |
| BaseAgent | ✅ | `agents/base.py` — clase base de todos los agentes |
| Job Match Agent | ✅ | Búsqueda semántica + reranking con Claude |
| Early Activation Agent | ✅ | Secuencia 72h, 5 pasos, mensajes personalizados |
| Event Bus (Redis) | ❌ eliminado | Reemplazado por polling sobre PostgreSQL |
| Docker | ❌ eliminado | 0 contenedores, ~1.6GB RAM liberada |

### Fase 2 — COMPLETA ✅ (5/5)

| # | Agente | Estado | Tests | Costo/op |
|---|---|---|---|---|
| 1 | Churn Predictor | ✅ Completado | 20/20 | ~$0.0004 (Haiku) |
| 2 | Re-engagement Agent | ✅ Completado | 18/18 | ~$0.010 (Sonnet) |
| 3 | Matching Notifier | ✅ Completado | 15/15 | ~$0.0002 (Haiku) |
| 4 | Profile Optimizer | ✅ Completado | 16/16 | ~$0.015 (Sonnet) |
| 5 | Employer Signal Agent | ✅ Completado | 16/16 | ~$0.001 (Haiku) |

**Total tests Fase 2: 85/85 ✅**

### Fase 3 — Parcialmente completada

| Opción | Estado | Descripción |
|---|---|---|
| **Email HTML** | ✅ Completado | Template responsivo con header navy #053d6a, CTA #2985c7, footer unsubscribe. Función `_build_html_email()` en `channels.py`. |
| **Monitoreo `/stats`** | ✅ Completado | `POSTGRES_URL` configurado en Railway. `GET /stats` y `GET /health` devuelven datos reales de uso y costos. |
| **WhatsApp** | ⏳ Pendiente | Conectar Meta Business API Sandbox (WHATSAPP_TOKEN en .env) |
| **Datos reales** | ⏳ Pendiente | Reemplazar mock_jobs/mock_users con datos reales de elempleo |

---

## Errores conocidos y fixes aplicados

### python vs python3 (Mac)
Usar siempre `python3 -m` en el Makefile. Mac no tiene `python` en PATH por defecto.

### Supabase: usar session pooler, no conexión directa
El hostname `db.[ref].supabase.co` no resuelve DNS en proyectos nuevos (2024+). Usar el **session pooler**:
```
# ✅ Correcto (session pooler)
postgresql://postgres.[project_ref]:[password]@aws-1-us-east-2.pooler.supabase.com:5432/postgres

# ❌ No resuelve en proyectos nuevos
postgresql://postgres:[password]@db.[ref].supabase.co:5432/postgres
```

### Qdrant Cloud: siempre pasar api_key
Qdrant local no requiere autenticación, pero Qdrant Cloud sí. Todos los clientes deben incluir `api_key`:
```python
QdrantClient(url=QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY"))
```
Archivos ya corregidos: `vector_db/embedder.py`, `vector_db/setup.py`, `scripts/load_data.py`.

### Health check: check_embeddings debe correr en thread
`sentence-transformers` carga el modelo de forma síncrona (~5s) y bloquea el event loop de asyncio. Si corre en `asyncio.gather` junto a conexiones de red, las otras corrutinas no pueden avanzar y fallan con timeout silencioso. Fix aplicado en `scripts/health_check.py`:
```python
results = await asyncio.to_thread(_run)  # no bloquea el event loop
```

### Qdrant: query_points() no search()
`qdrant-client >= 1.11` eliminó `search()`. Usar `query_points()`. Ya corregido en `vector_db/embedder.py`.

### Gateway: imports absolutos, no relativos
El gateway corre con `cd gateway && uvicorn main:app`. Los imports deben ser absolutos:
```python
from claude_client import ClaudeClient  # ✅
from .claude_client import ClaudeClient  # ❌ rompe
```

### Gateway: carga .env explícitamente
```python
load_dotenv(Path(__file__).parent.parent / ".env")  # en gateway/main.py
```

### UUIDs en mock data
`mock_jobs.json` usa IDs cortos (`job-001`). `scripts/load_data.py` convierte con `uuid.uuid5(uuid.NAMESPACE_DNS, raw_id)`. Mantener formato `job-XXX` / `user-XXX`.

### Early Activation: load_dotenv debe ir en channels.py, no en demo.py
`agents/early_activation/__init__.py` importa `EarlyActivationAgent` automáticamente al cargar el paquete. Esto ejecuta `channels.py` antes de que `demo.py` llegue a llamar `load_dotenv()`, dejando `MAILTRAP_TOKEN` vacío. Fix: `load_dotenv()` está al inicio de `channels.py`, antes de los `os.getenv()` de módulo.

### Early Activation: fallback de canal — usar is_channel_configured(), no instance.is_configured()
`get_channel(Channel.WHATSAPP)` devuelve `LogChannel` cuando WhatsApp no está configurado. `LogChannel.is_configured()` siempre retorna `True`, por lo que el fallback a email nunca se activaba. Fix: `is_channel_configured(channel)` en `channels.py` chequea el canal original sin el swap a LogChannel.

### Early Activation: demo hacía 2 llamadas LLM por paso
`_run_step_in_memory` genera + envía, y luego `demo.py` llamaba `_generate_message` de nuevo para mostrar el mensaje. Fix: `_run_step_in_memory` cachea el mensaje en `self._last_generated_message`; el demo lo reutiliza directamente.

---

## Dependencias clave

| Librería | Propósito |
|---|---|
| `anthropic` | SDK oficial de Claude API |
| `fastapi` + `uvicorn` | LLM Gateway y servidor de agentes |
| `asyncpg` | Cliente PostgreSQL async (Supabase) |
| `qdrant-client` | Vector DB para embeddings (Qdrant Cloud) |
| `sentence-transformers` | Embeddings locales (gratis, sin API key, soporta español) |
| `httpx` | HTTP client async para llamadas al Gateway |
| `structlog` | Logging estructurado |
| `rich` | Output visual en terminal (demos) |
| `tenacity` | Retries con backoff exponencial |
| `python-dotenv` | Carga de variables de entorno |
