"""
Matching Notifier — API REST

Endpoints:
  POST /agents/matching-notifier/process        — procesa vacantes nuevas
  POST /agents/matching-notifier/process/{job_id} — procesa vacante específica
  GET  /agents/matching-notifier/history        — historial de notificaciones
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from agents.matching_notifier.agent import MatchingNotifierAgent
from agents.matching_notifier.models import (
    BatchNotificationResult,
    JobNotificationResult,
    ProcessJobRequest,
    ProcessJobsRequest,
)
from cdp.events import CDPClient

load_dotenv(Path(__file__).parent.parent.parent / ".env")

router = APIRouter(prefix="/agents/matching-notifier", tags=["Matching Notifier"])


async def _get_agent() -> tuple[MatchingNotifierAgent, CDPClient, asyncpg.Pool]:
    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
    cdp    = CDPClient(postgres_url=pg_url)
    await cdp.connect()
    agent  = MatchingNotifierAgent(cdp=cdp, pool=pool)
    return agent, cdp, pool


@router.post("/process", response_model=BatchNotificationResult)
async def process_new_jobs(req: ProcessJobsRequest) -> BatchNotificationResult:
    """Procesa vacantes nuevas y notifica candidatos con alto match."""
    agent, cdp, pool = await _get_agent()
    try:
        return await agent.process_new_jobs(hours=req.hours)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()
        await cdp.close()
        await pool.close()


@router.post("/process/{job_id}", response_model=JobNotificationResult)
async def process_job(job_id: str) -> JobNotificationResult:
    """Procesa una vacante específica por ID."""
    agent, cdp, pool = await _get_agent()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM jobs WHERE id = $1::uuid AND is_active = TRUE", job_id
            )
        if not row:
            raise HTTPException(status_code=404, detail=f"Vacante {job_id} no encontrada")
        return await agent.process_job(dict(row))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()
        await cdp.close()
        await pool.close()


@router.get("/history")
async def get_history(limit: int = 50) -> dict:
    """Historial de notificaciones de matching enviadas."""
    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, properties, timestamp
                FROM events
                WHERE event_type = 'match.notification_sent'
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return {
            "total": len(rows),
            "notifications": [
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
