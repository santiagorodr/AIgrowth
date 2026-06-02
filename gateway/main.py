"""
LLM Gateway — FastAPI
──────────────────────
Punto de entrada único para todas las llamadas LLM del ecosistema.
Todos los agentes llaman a este servicio en lugar de llamar
directamente a la API de Claude.

Endpoints:
  POST /v1/complete   — Completion principal
  GET  /health        — Estado del gateway
  GET  /stats         — Uso y costos por agente (hoy)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Cargar .env desde la raíz del proyecto (un nivel arriba de /gateway)
load_dotenv(Path(__file__).parent.parent / ".env")

import asyncpg
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from claude_client import ClaudeClient
from models import CompletionRequest, CompletionResponse, HealthResponse

log = structlog.get_logger(__name__)

# ── Estado global del gateway ──────────────────────────────────────────────
_claude: ClaudeClient | None = None
_pg_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa conexiones al arrancar, las cierra al apagar."""
    global _claude, _pg_pool

    api_key = os.environ["ANTHROPIC_API_KEY"]
    postgres_url = os.environ.get(
        "POSTGRES_URL",
        "",
    )

    _claude = ClaudeClient(api_key=api_key)
    _pg_pool = await asyncpg.create_pool(postgres_url, min_size=2, max_size=5)

    log.info("gateway.started")
    yield

    if _pg_pool:
        await _pg_pool.close()
    log.info("gateway.stopped")


# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Elempleo LLM Gateway",
    description="Gateway centralizado para todas las llamadas LLM del AI Growth Engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── POST /v1/complete ──────────────────────────────────────────────────────
@app.post("/v1/complete", response_model=CompletionResponse)
async def complete(request: CompletionRequest) -> CompletionResponse:
    """
    Ejecuta una completion con Claude.
    El modelo se selecciona automáticamente según task_type:
    - generation/reasoning/conversation → Sonnet
    - classification/extraction        → Haiku
    """
    if not _claude:
        raise HTTPException(status_code=503, detail="Gateway no inicializado")

    try:
        response = await _claude.complete(request)

        # Registrar uso en agent_logs (no bloqueante)
        if _pg_pool:
            await _log_call(request, response)

        return response

    except Exception as e:
        log.error("gateway.complete_error", agent_id=request.agent_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /health ────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check del gateway y sus dependencias."""
    api_ok = await _claude.ping() if _claude else False

    # Stats del día
    calls_today = 0
    cost_today = 0.0
    if _pg_pool:
        async with _pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as calls, COALESCE(SUM(cost_usd), 0) as cost
                FROM agent_logs
                WHERE created_at::date = $1
                """,
                date.today(),
            )
            if row:
                calls_today = row["calls"]
                cost_today = float(row["cost"])

    return HealthResponse(
        status="ok" if api_ok else "degraded",
        anthropic_api="ok" if api_ok else "error",
        total_calls_today=calls_today,
        total_cost_today_usd=round(cost_today, 4),
    )


# ── GET /stats ─────────────────────────────────────────────────────────────
@app.get("/stats")
async def stats() -> dict:
    """Uso y costos desglosados por agente (últimos 7 días)."""
    if not _pg_pool:
        raise HTTPException(status_code=503, detail="DB no disponible")

    async with _pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                agent_id,
                COUNT(*) as total_calls,
                SUM(total_tokens) as total_tokens,
                ROUND(SUM(cost_usd)::numeric, 4) as total_cost_usd,
                ROUND(AVG(latency_ms)::numeric, 0) as avg_latency_ms,
                SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) as errors
            FROM agent_logs
            WHERE created_at >= NOW() - INTERVAL '7 days'
            GROUP BY agent_id
            ORDER BY total_cost_usd DESC
            """
        )
    return {
        "period": "last_7_days",
        "agents": [dict(r) for r in rows],
    }


# ── Helper: log a DB ────────────────────────────────────────────────────────
async def _log_call(request: CompletionRequest, response: CompletionResponse) -> None:
    try:
        async with _pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_logs
                    (agent_id, task_type, model_used, prompt_tokens, completion_tokens,
                     total_tokens, cost_usd, latency_ms, success)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                request.agent_id,
                request.task_type.value,
                response.usage.model_used,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
                response.usage.total_tokens,
                response.usage.cost_usd,
                response.usage.latency_ms,
                True,
            )
    except Exception as e:
        log.warning("gateway.log_failed", error=str(e))
