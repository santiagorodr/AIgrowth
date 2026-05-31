"""
Scheduler del Early Activation Agent
──────────────────────────────────────────────────────────────────────
Hace polling a PostgreSQL cada 30 segundos buscando pasos vencidos
(status='pending' AND scheduled_at <= NOW()) y los ejecuta.

Diseño:
  - asyncio puro — sin Celery ni workers externos en el POC
  - Cada tick procesa TODOS los pasos vencidos en lotes de MAX_BATCH
  - Usa SELECT ... FOR UPDATE SKIP LOCKED para evitar ejecuciones dobles
    si en el futuro se corren múltiples instancias del scheduler
  - En producción esto se reemplaza por Temporal.io workflows

Uso standalone:
    python -m agents.early_activation.scheduler

Uso embebido (en el servidor de agentes):
    scheduler = ActivationScheduler(pool=pool, agent=agent)
    asyncio.create_task(scheduler.run())
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)

# ── Configuración ──────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 30     # Frecuencia de polling
MAX_BATCH_SIZE        = 20     # Máx pasos por tick
MAX_CONCURRENT_STEPS  = 5      # Máx ejecuciones paralelas por tick


class ActivationScheduler:
    """
    Scheduler asíncrono para la secuencia de 72 horas.

    Parámetros:
        pool:  asyncpg Pool (requerido para leer/escribir onboarding_sequences)
        agent: EarlyActivationAgent (requerido para ejecutar los pasos)
        interval_seconds: segundos entre cada polling (default 30)
    """

    def __init__(self, pool, agent, interval_seconds: int = POLL_INTERVAL_SECONDS):
        self._pool     = pool
        self._agent    = agent
        self._interval = interval_seconds
        self._running  = False
        self._ticks    = 0
        self._total_executed  = 0
        self._total_failed    = 0

    # ── Ciclo principal ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Arranca el loop de polling. Corre indefinidamente hasta que
        se llame a stop() o llegue SIGTERM/SIGINT.
        """
        self._running = True
        log.info(
            "scheduler.started",
            interval_s=self._interval,
            max_batch=MAX_BATCH_SIZE,
        )

        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("scheduler.tick_error", error=str(exc))

            await asyncio.sleep(self._interval)

        log.info(
            "scheduler.stopped",
            ticks=self._ticks,
            total_executed=self._total_executed,
            total_failed=self._total_failed,
        )

    def stop(self) -> None:
        """Detiene el scheduler en el próximo tick."""
        self._running = False
        log.info("scheduler.stop_requested")

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        """
        Un ciclo de polling:
        1. Lee los pasos vencidos (SELECT FOR UPDATE SKIP LOCKED)
        2. Los ejecuta en paralelo (semáforo para limitar concurrencia)
        3. Loggea el resultado
        """
        self._ticks += 1
        due_steps = await self._fetch_due_steps()

        if not due_steps:
            log.debug("scheduler.tick", tick=self._ticks, due=0)
            return

        log.info("scheduler.tick", tick=self._ticks, due=len(due_steps))

        sem = asyncio.Semaphore(MAX_CONCURRENT_STEPS)

        async def _run_one(row: dict) -> None:
            async with sem:
                try:
                    result = await self._agent.execute_step(row)
                    if result.success:
                        self._total_executed += 1
                    else:
                        self._total_failed += 1
                except Exception as exc:
                    self._total_failed += 1
                    log.error(
                        "scheduler.step_error",
                        step=row.get("step"),
                        user_id=str(row.get("user_id")),
                        error=str(exc),
                    )

        await asyncio.gather(*[_run_one(row) for row in due_steps])

    # ── BD ────────────────────────────────────────────────────────────────────

    async def _fetch_due_steps(self) -> list[dict]:
        """
        Retorna los pasos vencidos listos para ejecutar.

        Usa FOR UPDATE SKIP LOCKED para que múltiples instancias del
        scheduler no tomen el mismo paso (seguro para escalar).
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, step, channel, status, metadata, scheduled_at
                    FROM onboarding_sequences
                    WHERE status     = 'pending'
                      AND scheduled_at <= NOW()
                    ORDER BY scheduled_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    MAX_BATCH_SIZE,
                )
                # Marcar como 'in_progress' para que no los tome otro worker
                if rows:
                    ids = [r["id"] for r in rows]
                    await conn.execute(
                        """
                        UPDATE onboarding_sequences
                        SET status = 'in_progress'
                        WHERE id = ANY($1::uuid[])
                        """,
                        ids,
                    )

        return [dict(r) for r in rows]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "ticks":           self._ticks,
            "total_executed":  self._total_executed,
            "total_failed":    self._total_failed,
            "running":         self._running,
            "interval_s":      self._interval,
        }


# ── Entrypoint standalone ──────────────────────────────────────────────────
async def _main() -> None:
    """
    Corre el scheduler en modo standalone.
    Requiere POSTGRES_URL y ANTHROPIC_API_KEY en el entorno.
    """
    import os

    import asyncpg

    from agents.early_activation.agent import EarlyActivationAgent
    from cdp.events import init_cdp
    from event_bus.bus import EventBus

    pg_url = os.getenv("POSTGRES_URL", "")

    log.info("scheduler.init", postgres=pg_url)

    pool = await asyncpg.create_pool(pg_url, min_size=2, max_size=5)
    cdp  = await init_cdp(pg_url)
    bus  = EventBus()

    agent     = EarlyActivationAgent(cdp=cdp, bus=bus, pool=pool)
    scheduler = ActivationScheduler(pool=pool, agent=agent)

    # Manejar SIGTERM y Ctrl-C de forma ordenada
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, scheduler.stop)

    await scheduler.run()
    await pool.close()


if __name__ == "__main__":
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    asyncio.run(_main())
