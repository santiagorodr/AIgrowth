"""
Re-engagement Agent — API REST
────────────────────────────────
Endpoints:
  POST /agents/reengagement/process           — procesa todos los pendientes
  POST /agents/reengagement/process/{user_id} — procesa usuario específico
  GET  /agents/reengagement/history           — historial de mensajes enviados
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from agents.reengagement.agent import ReengagementAgent
from agents.reengagement.models import (
    BatchSendResult,
    ProcessRequest,
    ProcessUserRequest,
    SendResult,
)
from cdp.events import CDPClient

load_dotenv(Path(__file__).parent.parent.parent / ".env")

router = APIRouter(prefix="/agents/reengagement", tags=["Re-engagement"])


async def _get_agent() -> tuple[ReengagementAgent, CDPClient, asyncpg.Pool]:
    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
    cdp    = CDPClient(postgres_url=pg_url)
    await cdp.connect()
    agent  = ReengagementAgent(cdp=cdp, pool=pool)
    return agent, cdp, pool


# ── POST /process ──────────────────────────────────────────────────────────────

@router.post("/process", response_model=BatchSendResult)
async def process_pending(req: ProcessRequest) -> BatchSendResult:
    """Procesa todos los usuarios con churn detectado pendientes de reactivar."""
    agent, cdp, pool = await _get_agent()
    try:
        return await agent.process_pending(limit=req.limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()
        await cdp.close()
        await pool.close()


# ── POST /process/{user_id} ────────────────────────────────────────────────────

@router.post("/process/{user_id}", response_model=SendResult)
async def process_user(user_id: str, req: ProcessUserRequest) -> SendResult:
    """Genera y envía un mensaje de reactivación para un usuario específico."""
    agent, cdp, pool = await _get_agent()
    try:
        churn_data = {
            "risk_level":    req.risk_level,
            "risk_reason":   req.risk_reason,
            "days_inactive": req.days_inactive,
            "key_signals":   [],
        }
        return await agent.process_user(user_id=user_id, churn_data=churn_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()
        await cdp.close()
        await pool.close()


# ── GET /history ───────────────────────────────────────────────────────────────

@router.get("/history")
async def get_history(limit: int = 50) -> dict:
    """Retorna el historial de mensajes de reactivación enviados."""
    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, properties, timestamp
                FROM events
                WHERE event_type = 'reengagement.message_sent'
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return {
            "total": len(rows),
            "messages": [
                {
                    "user_id":   str(r["user_id"]),
                    "timestamp": r["timestamp"].isoformat(),
                    **(r["properties"] if isinstance(r["properties"], dict) else {}),
                }
                for r in rows
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await pool.close()
