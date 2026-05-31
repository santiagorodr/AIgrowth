"""
Churn Predictor — API REST
───────────────────────────
Expone el agente como endpoints FastAPI.

Endpoints:
  POST /agents/churn-predictor/analyze        — analiza un usuario por ID
  POST /agents/churn-predictor/analyze-batch  — analiza todos los inactivos
  GET  /agents/churn-predictor/risks          — lista eventos churn del CDP
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from agents.churn_predictor.agent import ChurnPredictorAgent
from agents.churn_predictor.models import (
    AnalyzeBatchRequest,
    AnalyzeRequest,
    BatchResult,
    ChurnAnalysis,
)
from cdp.events import CDPClient

load_dotenv(Path(__file__).parent.parent.parent / ".env")

router = APIRouter(prefix="/agents/churn-predictor", tags=["Churn Predictor"])


async def _get_agent_with_cdp() -> tuple[ChurnPredictorAgent, CDPClient, asyncpg.Pool]:
    """Crea agente con CDP conectado a Supabase."""
    postgres_url = os.getenv("POSTGRES_URL", "")
    pool = await asyncpg.create_pool(postgres_url, min_size=1, max_size=3)
    cdp  = CDPClient(postgres_url=postgres_url)
    await cdp.connect()
    agent = ChurnPredictorAgent(cdp=cdp)
    return agent, cdp, pool


# ── POST /analyze ──────────────────────────────────────────────────────────────

@router.post("/analyze", response_model=ChurnAnalysis)
async def analyze_user(req: AnalyzeRequest) -> ChurnAnalysis:
    """
    Analiza el riesgo de churn de un usuario específico.
    Requiere que el usuario exista en la tabla `users` de Supabase.
    """
    agent, cdp, pool = await _get_agent_with_cdp()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE id = $1::uuid", req.user_id
            )
        if not row:
            raise HTTPException(status_code=404, detail=f"Usuario {req.user_id} no encontrado")

        return await agent.analyze_user(dict(row))

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()
        await cdp.close()
        await pool.close()


# ── POST /analyze-batch ────────────────────────────────────────────────────────

@router.post("/analyze-batch", response_model=BatchResult)
async def analyze_batch(req: AnalyzeBatchRequest) -> BatchResult:
    """
    Analiza todos los usuarios inactivos en los últimos N días.
    Útil para correr manualmente o desde el scheduler.
    """
    agent, cdp, pool = await _get_agent_with_cdp()
    try:
        return await agent.analyze_batch(days_inactive=req.days_inactive)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()
        await cdp.close()
        await pool.close()


# ── GET /risks ─────────────────────────────────────────────────────────────────

@router.get("/risks")
async def get_detected_risks(limit: int = 50) -> dict:
    """
    Retorna los últimos eventos `churn.risk_detected` del CDP.
    Útil para dashboard y monitoreo.
    """
    postgres_url = os.getenv("POSTGRES_URL", "")
    pool = await asyncpg.create_pool(postgres_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, properties, timestamp
                FROM events
                WHERE event_type = 'churn.risk_detected'
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return {
            "total": len(rows),
            "risks": [
                {
                    "user_id":   str(r["user_id"]),
                    "timestamp": r["timestamp"].isoformat(),
                    **r["properties"] if isinstance(r["properties"], dict) else {},
                }
                for r in rows
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await pool.close()
