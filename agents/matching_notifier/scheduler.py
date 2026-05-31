"""
Matching Notifier Scheduler — polling cada 6 horas

Uso standalone:
    python -m agents.matching_notifier.scheduler
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 21600   # 6 horas


class MatchingScheduler:
    def __init__(self, cdp, pool, agent, interval_seconds: int = POLL_INTERVAL_SECONDS):
        self._cdp        = cdp
        self._pool       = pool
        self._agent      = agent
        self._interval   = interval_seconds
        self._running    = False
        self._ticks      = 0
        self._total_notified = 0
        self._last_run: datetime | None = None

    async def run(self) -> None:
        self._running = True
        log.info("matching_scheduler.started", interval_s=self._interval)
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("matching_scheduler.tick_error", error=str(exc))
            await asyncio.sleep(self._interval)
        log.info("matching_scheduler.stopped", ticks=self._ticks, notified=self._total_notified)

    def stop(self) -> None:
        self._running = False

    async def _tick(self) -> None:
        self._ticks   += 1
        self._last_run = datetime.now(timezone.utc)
        log.info("matching_scheduler.tick", tick=self._ticks)
        result = await self._agent.process_new_jobs(hours=6)
        self._total_notified += result.total_notified
        log.info("matching_scheduler.tick_done", tick=self._ticks,
                 jobs=result.jobs_processed, notified=result.total_notified)

    def stats(self) -> dict:
        return {
            "ticks":           self._ticks,
            "total_notified":  self._total_notified,
            "running":         self._running,
            "interval_s":      self._interval,
            "last_run":        self._last_run.isoformat() if self._last_run else None,
        }


async def _main() -> None:
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    import asyncpg
    from agents.matching_notifier.agent import MatchingNotifierAgent
    from cdp.events import CDPClient

    pg_url = os.getenv("POSTGRES_URL", "")
    pool   = await asyncpg.create_pool(pg_url, min_size=2, max_size=5)
    cdp    = CDPClient(postgres_url=pg_url)
    await cdp.connect()
    agent  = MatchingNotifierAgent(cdp=cdp, pool=pool)
    scheduler = MatchingScheduler(cdp=cdp, pool=pool, agent=agent)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, scheduler.stop)

    await scheduler.run()
    await pool.close()
    await cdp.close()


if __name__ == "__main__":
    import structlog
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ])
    asyncio.run(_main())
