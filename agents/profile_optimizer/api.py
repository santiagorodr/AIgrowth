"""
Profile Optimizer — API REST

POST /agents/profile-optimizer/analyze-batch     — analiza usuarios con perfil incompleto
POST /agents/profile-optimizer/analyze/{user_id} — analiza usuario específico
GET  /agents/profile-optimizer/history           — historial de optimizaciones enviadas
"""
from __future__ import annotations
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from agents.profile_optimizer.agent import ProfileOptimizerAgent
from agents.profile_optimizer.models import (
    AnalyzeBatchRequest,
    BatchOptimizationResult, OptimizationReport,
)
from cdp.events import CDPClient

load_dotenv(Path(__file__).parent.parent.parent / ".env")
router = APIRouter(prefix="/agents/profile-optimizer", tags=["Profile Optimizer"])


async def _get_agent() -> tuple[ProfileOptimizerAgent, CDPClient, asyncpg.Pool]:
    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
    cdp    = CDPClient(postgres_url=pg_url)
    await cdp.connect()
    return ProfileOptimizerAgent(cdp=cdp, pool=pool), cdp, pool


@router.post("/analyze-batch", response_model=BatchOptimizationResult)
async def analyze_batch(req: AnalyzeBatchRequest) -> BatchOptimizationResult:
    agent, cdp, pool = await _get_agent()
    try:
        return await agent.analyze_batch(max_completion=req.max_completion)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close(); await cdp.close(); await pool.close()


@router.post("/analyze/{user_id}", response_model=OptimizationReport)
async def analyze_user(user_id: str) -> OptimizationReport:
    agent, cdp, pool = await _get_agent()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1::uuid", user_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Usuario {user_id} no encontrado")
        return await agent.analyze_user(dict(row))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close(); await cdp.close(); await pool.close()


@router.get("/history")
async def get_history(limit: int = 50) -> dict:
    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, properties, timestamp
                FROM events
                WHERE event_type = 'profile.optimization_suggested'
                ORDER BY timestamp DESC LIMIT $1
                """, limit,
            )
        return {
            "total": len(rows),
            "optimizations": [
                {"user_id": str(r["user_id"]), "timestamp": r["timestamp"].isoformat(),
                 **(r["properties"] if isinstance(r["properties"], dict) else {})}
                for r in rows
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await pool.close()
