# HANDOFF — Elempleo AI Growth Engine
> Para: Claude Code  
> De: sesión Fase 2 completa — 5/5 agentes verificados
> Fecha: 2026-05-31
> Estado: Fase 1 ✅ + Fase 2 ✅ COMPLETAS. 85/85 tests pasando. Próximos pasos: pruebas en real, Railway deploy o integraciones de canal.

---

## Quién es el usuario

**Santiago Rodríguez** — Product Manager Senior en **elempleo.com**, principal portal de empleo de Colombia. No es desarrollador. Construyó este proyecto con asistencia de Claude paso a paso.

**Su objetivo:** ecosistema multi-agente de IA que adquiera, active y retenga candidatos sin equipo comercial.

**Cómo trabaja:** orientado a negocio, no técnico. Necesita explicaciones claras, comandos exactos, y advertencias antes de que algo pueda romper. Respóndele siempre en **español**.

---

## Estado actual del proyecto

### ✅ Fase 1 — COMPLETA, verificada y migrada a cloud

Stack 100% operativo en cloud. Verificado con `make test` — 4/4 servicios en verde.

| Componente | Estado | Detalle |
|---|---|---|
| PostgreSQL → **Supabase** | ✅ | 25 vacantes, 20 usuarios cargados |
| Redis → **Eliminado** | ✅ | Reemplazado por polling PostgreSQL |
| Qdrant → **Qdrant Cloud** | ✅ | `elempleo_jobs` (25 pts) + `elempleo_users` indexadas |
| LLM Gateway (FastAPI :8000) | ✅ | Proceso Python local, Anthropic API conectada |
| Embeddings semánticos | ✅ | `make test` devuelve resultados con score >0.5 |
| BaseAgent | ✅ | `agents/base.py` — clase base de todos los agentes |
| Job Match Agent | ✅ | Búsqueda semántica + reranking LLM (~$0.023/búsqueda) |
| Early Activation Agent | ✅ | Secuencia 72h, 5/5 pasos, 0 fallidos |
| Docker Desktop | ✅ eliminado | ~1.6GB RAM liberada en el M1 |

### ✅ Fase 2 — COMPLETA (5/5)

| # | Agente | Estado | Tests | Scheduler |
|---|---|---|---|---|
| 1 | **Churn Predictor** | ✅ Completado | 20/20 | Cada hora |
| 2 | **Re-engagement Agent** | ✅ Completado | 18/18 | Cada 30 min, dedup 72h |
| 3 | **Matching Notifier** | ✅ Completado | 15/15 | Cada 6h, dedup 72h |
| 4 | **Profile Optimizer** | ✅ Completado | 16/16 | Diario (24h) |
| 5 | **Employer Signal Agent** | ✅ Completado | 16/16 | Cada 15 min, dedup 24h |

**Total: 85/85 tests pasando ✅**

---

## Cómo levantar el stack (instrucciones exactas)

```bash
# Solo necesitas UNA terminal
cd ~/Documents/Claude/"Growth agents personas EE"/elempleo-ai-growth

# 1. Verificar que los servicios cloud están vivos
make test

# 2. Levantar el LLM Gateway (queda bloqueada, es normal)
make gateway-dev

# En otra terminal, si quieres correr demos o agentes:
make demo-job-match
make demo-activation-offline
```

**Si Qdrant Cloud queda vacío** (raro, pero puede pasar tras migración):
```bash
python3 -m vector_db.setup    # re-crea colecciones
python3 -m scripts.load_data  # re-indexa las 25 vacantes
```

**No hay Docker.** No hay `make start`. No hay Redis. Todo es cloud + proceso Python.

---

## Arquitectura — decisiones de diseño

### 1. LLM Gateway centralizado (proceso Python local)
Todos los agentes llaman a `http://localhost:8000/v1/complete`. Routing automático:

```python
MODEL_ROUTING = {
    "generation":     "claude-sonnet-4-6",
    "reasoning":      "claude-sonnet-4-6",
    "conversation":   "claude-sonnet-4-6",
    "classification": "claude-haiku-4-5-20251001",
    "extraction":     "claude-haiku-4-5-20251001",
}
```

### 2. BaseAgent — todos los agentes heredan de aquí
```python
class MiAgente(BaseAgent):
    AGENT_ID = "mi_agente"

    async def run(self, input: dict) -> dict:
        user_id = input.get("user_id")
        await self.log_run("started", user_id=user_id)

        resultado = await self.llm(
            task_type="classification",  # Haiku (barato)
            system="Eres un experto en...",
            user_message=f"Analiza: {input}",
            max_tokens=200,
            temperature=0.0,
        )

        await self.track(Events.AGENT_COMPLETED, user_id=user_id,
                         properties={"resultado": resultado})
        await self.log_run("completed", user_id=user_id)
        return {"content": resultado}
```

`self.llm()`, `self.track()`, `self.publish()` y `self.log_run()` están en `agents/base.py`. No repetir.

### 3. CDP es append-only (PostgreSQL / Supabase)
La tabla `events` **nunca se actualiza, solo se inserta**. Los agentes de Fase 2 consultan esta tabla para detectar condiciones (inactividad, riesgo de churn, etc.) mediante polling periódico.

### 4. Event Bus eliminado — arquitectura polling
Redis fue eliminado. Los agentes de Fase 2 no reaccionan a eventos en tiempo real; en su lugar ejecutan ciclos periódicos consultando PostgreSQL directamente.

Patrón para agentes con scheduler:
```python
# Mismo patrón que agents/early_activation/scheduler.py
SELECT ... FROM users WHERE ... FOR UPDATE SKIP LOCKED
```

### 5. Qdrant Cloud — sentence-transformers local
El modelo `paraphrase-multilingual-MiniLM-L12-v2` corre localmente (~470MB, gratis, soporta español). Los vectores se suben a Qdrant Cloud. El cliente **siempre necesita `api_key`**:
```python
QdrantClient(url=QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY"))
```

### 6. POC mode — funciona sin CDP
Si `cdp=None` en BaseAgent, el tracking se omite silenciosamente. Permite tests sin conexión a Supabase.

---

## Errores conocidos y fixes aplicados

**CRÍTICO — leer antes de hacer cualquier cambio:**

### Supabase: session pooler, no conexión directa
```
# ✅ Funciona (session pooler — nuevo formato 2024)
postgresql://postgres.[project_ref]:[pwd]@aws-1-us-east-2.pooler.supabase.com:5432/postgres

# ❌ No resuelve DNS en proyectos nuevos
postgresql://postgres:[pwd]@db.[ref].supabase.co:5432/postgres
```

### Qdrant Cloud: api_key obligatorio
Sin `api_key`, Qdrant Cloud retorna `403 Forbidden`. Ya corregido en:
- `vector_db/embedder.py`
- `vector_db/setup.py`
- `scripts/load_data.py`

### Health check: check_embeddings en asyncio.to_thread
`sentence-transformers` bloquea el event loop ~5s al cargar el modelo. Si corre en `asyncio.gather` junto a conexiones de red, las otras corrutinas fallan silenciosamente. Fix en `scripts/health_check.py`:
```python
results = await asyncio.to_thread(_run)
```

### python vs python3
Mac usa `python3`. El Makefile ya usa `python3 -m` en todos los targets.

### Gateway: imports absolutos
```python
from claude_client import ClaudeClient   # ✅
from .claude_client import ClaudeClient  # ❌
```

### Gateway: carga .env explícitamente
```python
load_dotenv(Path(__file__).parent.parent / ".env")
```

### UUIDs en mock data
`mock_jobs.json` usa IDs cortos. `scripts/load_data.py` convierte con `uuid.uuid5`. Mantener formato `job-XXX` / `user-XXX`.

### Qdrant: query_points() no search()
`qdrant-client >= 1.11` eliminó `search()`. Usar `query_points()`. Ya corregido.

### scripts/: cargar .env con path explícito
Los scripts en `scripts/` deben cargar el `.env` desde la raíz del proyecto:
```python
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")
```
Sin esto, las variables de entorno no se encuentran al correr como módulo.

---

## Estructura de archivos actualizada

```
elempleo-ai-growth/
├── CLAUDE.md                  ← documentación técnica (leer primero)
├── HANDOFF.md                 ← este archivo
├── .env                       ← credenciales reales (no commitear)
├── .env.example               ← plantilla documentada
├── Makefile                   ← todos los comandos (sin Docker)
├── requirements.txt           ← dependencias Python
├── docker-compose.yml.bak     ← ARCHIVADO — ya no se usa
│
├── agents/
│   ├── base.py                ← BaseAgent (leer antes de crear agente nuevo)
│   ├── server.py              ← FastAPI principal, registra todos los routers
│   ├── job_match/             ← JobMatchAgent: semántica + reranking LLM
│   ├── early_activation/      ← EarlyActivationAgent: secuencia 72h
│   ├── churn_predictor/       ← ChurnPredictorAgent: clasifica riesgo HIGH/MEDIUM/LOW
│   ├── reengagement/          ← ReengagementAgent: mensajes personalizados, dedup 72h
│   ├── matching_notifier/     ← MatchingNotifierAgent: búsqueda inversa Qdrant, dedup 72h
│   ├── profile_optimizer/     ← ProfileOptimizerAgent: gap analysis perfil vs vacantes
│   └── employer_signal/       ← EmployerSignalAgent: señales de empleadores + simulador POC
│
├── cdp/
│   ├── events.py              ← CDPClient (solo PostgreSQL, sin Redis) + Events
│   └── schema.sql             ← schema completo (ya aplicado en Supabase)
│
├── event_bus/
│   └── bus.py                 ← STUB no-op (Redis eliminado, mantiene interfaz)
│
├── gateway/
│   ├── main.py                ← LLM Gateway FastAPI
│   ├── claude_client.py       ← routing Haiku/Sonnet + retries + cost tracking
│   └── models.py              ← Pydantic models del gateway
│
├── vector_db/
│   ├── embedder.py            ← JobEmbedder → Qdrant Cloud
│   └── setup.py               ← inicializa colecciones en Qdrant Cloud
│
├── scripts/
│   ├── load_data.py           ← carga datos a Supabase + Qdrant Cloud
│   ├── health_check.py        ← verifica 4 servicios (sin Redis)
│   ├── verify_agent.py        ← tests Job Match Agent
│   └── verify_early_activation.py ← 66 tests Early Activation
│
└── data/
    ├── mock_jobs.json         ← 25 vacantes colombianas
    └── mock_users.json        ← 20 perfiles de candidatos
```

---

## Patrón para construir un agente Fase 2

```
agents/nombre_agente/
├── __init__.py
├── agent.py     ← lógica core (hereda BaseAgent)
├── api.py       ← router FastAPI
├── demo.py      ← demo CLI con Rich
├── models.py    ← Pydantic models
└── prompts.py   ← system prompts
```

Registrar en `agents/server.py`:
```python
from agents.nuevo_agente.api import router as nuevo_router
app.include_router(nuevo_router, prefix="/agents/nuevo-agente", tags=["NuevoAgente"])
```

Agregar al Makefile:
```makefile
demo-nuevo: ## Demo del Nuevo Agente
	python3 -m agents.nuevo_agente.demo

verify-nuevo: ## Verifica el Nuevo Agente
	python3 scripts/verify_nuevo_agente.py
```

---

## Schema PostgreSQL — queries útiles para Fase 2

```sql
-- Churn Predictor: usuarios sin actividad reciente
SELECT id, email, full_name, last_active_at, city, skills
FROM users
WHERE is_active = TRUE
  AND last_active_at < NOW() - INTERVAL '30 days'
ORDER BY last_active_at ASC;

-- Re-engagement: historial de eventos de un usuario
SELECT event_type, properties, timestamp
FROM events
WHERE user_id = $1
ORDER BY timestamp DESC
LIMIT 50;

-- Matching Notifier: usuarios por ciudad/categoría para vacante nueva
SELECT id, email, city, skills, profile_completion
FROM users
WHERE is_active = TRUE
  AND city = $1;

-- Profile Optimizer: usuarios con perfil incompleto
SELECT id, profile_completion, skills, education_level, experience_years
FROM users
WHERE profile_completion < 70
  AND is_active = TRUE;

-- Employer Signal: vacantes nuevas por ciudad/categoría
SELECT city, category, COUNT(*) as nuevas_vacantes
FROM jobs
WHERE is_active = TRUE
  AND published_at > NOW() - INTERVAL '7 days'
GROUP BY city, category;

-- Costos por agente (monitoreo Fase 2)
SELECT agent_id, COUNT(*) as llamadas, SUM(cost_usd) as costo_total_usd
FROM agent_logs
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY agent_id
ORDER BY costo_total_usd DESC;
```

---

## Catálogo de eventos CDP

```python
# cdp/events.py — clase Events
# Siempre agregar eventos nuevos aquí, no hardcodear strings

# Ya definidos (Fase 1):
Events.USER_REGISTERED          # "user.registered"
Events.USER_BECAME_INACTIVE     # "user.became_inactive"
Events.USER_REACTIVATED         # "user.reactivated"
Events.JOB_VIEWED               # "job.viewed"
Events.JOB_APPLIED              # "job.applied"
Events.ACTIVATION_STEP_SENT     # "activation.step_sent"
Events.ACTIVATION_STEP_SKIPPED  # "activation.step_skipped"
Events.AGENT_TRIGGERED          # "agent.triggered"
Events.AGENT_COMPLETED          # "agent.completed"
Events.AGENT_ERROR              # "agent.error"

# Por agregar en Fase 2 (en cdp/events.py antes de usar):
# Events.CHURN_RISK_DETECTED        = "churn.risk_detected"
# Events.REENGAGEMENT_SENT          = "reengagement.message_sent"
# Events.MATCH_NOTIFICATION_SENT    = "match.notification_sent"
# Events.PROFILE_OPTIMIZATION_SENT  = "profile.optimization_suggested"
# Events.EMPLOYER_SIGNAL_DETECTED   = "employer.signal_detected"
```

---

## Costo real observado (baseline)

| Operación | Modelo | Tokens aprox | Costo USD |
|---|---|---|---|
| Job Match (16 candidatos, reranking) | Sonnet | ~3,150 | $0.023 |
| Early Activation step (generación) | Sonnet | ~1,130 | $0.010 |
| Secuencia 72h completa (5 pasos) | Sonnet | ~5,500 | ~$0.050 |
| Clasificación simple (Haiku) | Haiku | ~200 | $0.0003 |
| **Churn Predictor estimado** (clasificación) | Haiku | ~300 | ~$0.0004 |

---

## Infraestructura adicional configurada (2026-05-31)

| Herramienta | Propósito | Notas |
|---|---|---|
| GitHub | Control de versiones | [github.com/santiagorodr/AIgrowth](https://github.com/santiagorodr/AIgrowth) — privado |
| ngrok | URL pública para pruebas | `ngrok http 8000` — URL temporal, cambia al reiniciar |
| gh CLI | Autenticación GitHub | Token guardado en Keychain Mac, no expira |

---

## Próxima sesión — por dónde empezar

**Comandos de arranque (ejecutar en orden):**

```bash
# Terminal 1 — verificar stack y levantar Gateway (queda bloqueada)
cd ~/Documents/Claude/"Growth agents personas EE"/elempleo-ai-growth
make test
make gateway-dev

# Terminal 2 — solo si necesitas URL pública para pruebas
ngrok http 8000
```

**Fase 2 completa. No hay agentes pendientes.**

### Parámetros configurables en .env

| Variable | Default | Agente |
|---|---|---|
| `MATCHING_JOB_WINDOW_HOURS` | `6` | Matching Notifier — ventana de vacantes nuevas |
| `MATCHING_DEDUP_HOURS` | `72` | Matching Notifier — antiduplicados |

### Opciones de continuación (en orden de impacto sugerido)

1. **Pruebas en real con Claude** — correr `make demo-employer` (sin `--no-llm`) con Gateway activo para ver mensajes reales generados por Haiku/Sonnet
2. **Conectar canales reales** — Mailtrap (email sandbox gratuito) o WhatsApp Meta Sandbox para recibir notificaciones reales en vez de LogChannel
3. **Railway deploy** — desplegar el sistema para que corra sin depender de la Mac encendida
4. **Datos reales de elempleo** — reemplazar mock_jobs/mock_users con datos reales del portal
