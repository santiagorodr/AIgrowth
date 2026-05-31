"""
Event Bus — Stub (sin Redis)
──────────────────────────────
El Event Bus fue eliminado en la migración a arquitectura cloud ligera.
Los agentes de Fase 2 usan polling sobre PostgreSQL en lugar de pub/sub.

Esta interfaz se mantiene para que los imports existentes no rompan.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine


# ── Canales (constantes de referencia — ya no se publican en Redis) ──────────
class Channels:
    USERS        = "elempleo:users"
    CONVERSIONS  = "elempleo:conversions"
    RETENTION    = "elempleo:retention"
    WHATSAPP     = "elempleo:whatsapp"
    INTELLIGENCE = "elempleo:intelligence"
    GROWTH       = "elempleo:growth"
    CONTENT      = "elempleo:content"
    GENERAL      = "elempleo:general"


# ── EventBus stub ────────────────────────────────────────────────────────────
class EventBus:
    """Stub no-op. Mantiene la interfaz para que el código existente no rompa."""

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def publish(self, channel: str, event: str, data: dict[str, Any], agent_id: str | None = None) -> int:
        return 0

    def subscribe(self, channel: str, handler: Any) -> None:
        pass

    def on(self, channel: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            return fn
        return decorator

    async def start(self) -> None:
        pass


# ── Singleton global ─────────────────────────────────────────────────────────
_bus: EventBus | None = None


def get_bus() -> EventBus:
    if _bus is None:
        raise RuntimeError("EventBus no inicializado. Llama a init_bus().")
    return _bus


async def init_bus() -> EventBus:
    global _bus
    _bus = EventBus()
    return _bus
