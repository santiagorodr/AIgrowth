"""
Employer Signal — API REST

POST /agents/employer-signal/process          ← procesa señales pendientes
POST /agents/employer-signal/simulate         ← genera N señales mock
POST /agents/employer-signal/notify/{user_id} ← notifica manualmente
GET  /agents/employer-signal/history          ← historial de señales enviadas
"""
from __future__ import annotations
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from agents.employer_signal.agent import EmployerSignalAgent
from agents.employer_signal.models import (
    BatchSignalResult, ProcessRequest, SimulateRequest,
)
from cdp.events import CDPClient

load_dotenv(Path(__file__).parent.parent.parent / ".env")
router = APIRouter(prefix="/agents/employer-signal", tags=["Employer Signal"])


async def _get_agent() -> tuple[EmployerSignalAgent, CDPClient, asyncpg.Pool]:
    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
    cdp    = CDPClient(postgres_url=pg_url); await cdp.connect()
    return EmployerSignalAgent(cdp=cdp, pool=pool), cdp, pool


@router.post("/process", response_model=BatchSignalResult)
async def process_pending(req: ProcessRequest) -> BatchSignalResult:
    """Procesa señales de employer views de los últimos N minutos."""
    agent, cdp, pool = await _get_agent()
    try:
        return await agent.process_pending(window_minutes=req.window_minutes)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close(); await cdp.close(); await pool.close()


@router.post("/simulate")
async def simulate_views(req: SimulateRequest) -> dict:
    """Genera N señales mock employer.viewed_profile en el CDP (solo POC)."""
    agent, cdp, pool = await _get_agent()
    try:
        count = await agent.simulate_employer_views(n=req.n)
        return {"created": count, "message": f"{count} señales mock generadas en el CDP"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close(); await cdp.close(); await pool.close()


@router.post("/notify/{user_id}")
async def notify_user(user_id: str, company_name: str = "Una empresa") -> dict:
    """Notifica manualmente a un usuario que una empresa vio su perfil."""
    agent, cdp, pool = await _get_agent()
    try:
        user = await agent._get_user_profile(user_id)
        if not user:
            raise HTTPException(status_code=404, detail=f"Usuario {user_id} no encontrado")
        view = {"company_name": company_name, "job_title_viewed": "", "view_duration_seconds": 60}
        text = await agent._generate_notification(user, view)
        success, msg_id = await agent._send(user, view, text)
        return {"success": success, "message_id": msg_id, "preview": text[:100]}
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
                SELECT user_id, properties, timestamp FROM events
                WHERE event_type = 'employer.signal_notified'
                ORDER BY timestamp DESC LIMIT $1
                """, limit,
            )
        return {
            "total": len(rows),
            "signals": [
                {"user_id": str(r["user_id"]), "timestamp": r["timestamp"].isoformat(),
                 **(r["properties"] if isinstance(r["properties"], dict) else {})}
                for r in rows
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await pool.close()
