"""
Agents Server — FastAPI principal
───────────────────────────────────
Servidor que expone todos los agentes como API REST.
Cada agente tiene su propio router; este archivo los monta.

Arrancar:
    uvicorn agents.server:app --reload --port 8001

Docs interactivas: http://localhost:8001/docs
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.churn_predictor.api import router as churn_predictor_router
from agents.early_activation.api import router as early_activation_router
from agents.employer_signal.api import router as employer_signal_router
from agents.job_match.api import router as job_match_router
from agents.matching_notifier.api import router as matching_notifier_router
from agents.profile_optimizer.api import router as profile_optimizer_router
from agents.reengagement.api import router as reengagement_router

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("agents_server.started")
    yield
    log.info("agents_server.stopped")


app = FastAPI(
    title="Elempleo — Agents API",
    description="API REST de todos los agentes del AI Growth Engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Montar routers de agentes ─────────────────────────────────────────────────
app.include_router(job_match_router)
app.include_router(early_activation_router)
app.include_router(churn_predictor_router)
app.include_router(reengagement_router)
app.include_router(matching_notifier_router)
app.include_router(profile_optimizer_router)
app.include_router(employer_signal_router)


@app.get("/")
async def root():
    return {
        "service": "Elempleo Agents API",
        "agents": ["job_match_agent", "early_activation_agent", "churn_predictor", "reengagement_agent", "matching_notifier", "profile_optimizer", "employer_signal"],
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
