"""
Churn Predictor Scheduler
──────────────────────────────────────────────────────────────────────────
Polling cada hora sobre PostgreSQL (Supabase) buscando usuarios inactivos
y ejecutando el análisis de churn sobre ellos.

Diseño:
  - asyncio puro — sin Celery ni workers externos
  - Corre cada 3600 segundos (1 hora) por defecto
  - No usa FOR UPDATE SKIP LOCKED (no hay tabla de cola)
    En su lugar, verifica si ya existe un evento reciente para no duplicar

Uso standalone:
    python -m agents.churn_predictor.scheduler

Uso embebido:
    scheduler = ChurnScheduler(cdp=cdp, agent=agent)
    asyncio.create_task(scheduler.run())
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 3600   # cada hora
DEFAULT_DAYS_INACTIVE = 7      # umbral de inactividad


class ChurnScheduler:
    """
    Scheduler que corre el Churn Predictor periódicamente.

    Parámetros:
        cdp:    CDPClient conectado a Supabase
        agent:  ChurnPredictorAgent
        interval_seconds: segundos entre cada ciclo (default 3600 = 1h)
        days_inactive: umbral de inactividad para considerar a un usuario en riesgo
    """

    def __init__(
        self,
        cdp,
        agent,
        interval_seconds: int = POLL_INTERVAL_SECONDS,
        days_inactive: int = DEFAULT_DAYS_INACTIVE,
    ):
        self._cdp           = cdp
        self._agent         = agent
        self._interval      = interval_seconds
        self._days_inactive = days_inactive
        self._running       = False
        self._ticks         = 0
        self._total_analyzed = 0
        self._high_risk_found = 0
        self._last_run: datetime | None = None

    # ── Ciclo principal ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Arranca el loop de polling. Corre indefinidamente hasta stop()."""
        self._running = True
        log.info(
            "churn_scheduler.started",
            interval_s=self._interval,
            days_inactive=self._days_inactive,
        )

        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("churn_scheduler.tick_error", error=str(exc))

            await asyncio.sleep(self._interval)

        log.info(
            "churn_scheduler.stopped",
            ticks=self._ticks,
            total_analyzed=self._total_analyzed,
            high_risk_found=self._high_risk_found,
        )

    def stop(self) -> None:
        """Detiene el scheduler en el próximo tick."""
        self._running = False
        log.info("churn_scheduler.stop_requested")

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        """Un ciclo de análisis: analiza todos los usuarios inactivos."""
        self._ticks += 1
        self._last_run = datetime.now(timezone.utc)

        log.info(
            "churn_scheduler.tick",
            tick=self._ticks,
            days_inactive=self._days_inactive,
        )

        result = await self._agent.analyze_batch(days_inactive=self._days_inactive)

        self._total_analyzed += result.total_analyzed
        self._high_risk_found += result.high_risk

        log.info(
            "churn_scheduler.tick_done",
            tick=self._ticks,
            analyzed=result.total_analyzed,
            high=result.high_risk,
            medium=result.medium_risk,
            low=result.low_risk,
        )

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "ticks":            self._ticks,
            "total_analyzed":   self._total_analyzed,
            "high_risk_found":  self._high_risk_found,
            "running":          self._running,
            "interval_s":       self._interval,
            "days_inactive":    self._days_inactive,
            "last_run":         self._last_run.isoformat() if self._last_run else None,
        }


# ── Entrypoint standalone ──────────────────────────────────────────────────────
async def _main() -> None:
    """Corre el scheduler en modo standalone."""
    import os
    from pathlib import Path

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    from agents.churn_predictor.agent import ChurnPredictorAgent
    from cdp.events import CDPClient

    pg_url = os.getenv("POSTGRES_URL", "")
    cdp    = CDPClient(postgres_url=pg_url)
    await cdp.connect()

    agent     = ChurnPredictorAgent(cdp=cdp)
    scheduler = ChurnScheduler(cdp=cdp, agent=agent)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, scheduler.stop)

    await scheduler.run()
    await cdp.close()


if __name__ == "__main__":
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    asyncio.run(_main())
