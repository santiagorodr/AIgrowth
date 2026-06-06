"""
BaseAgent — Clase base para todos los agentes del ecosistema
─────────────────────────────────────────────────────────────
Provee:
  - self.llm()     → llama al LLM Gateway (con routing Sonnet/Haiku automático)
  - self.track()   → registra eventos en CDP + Event Bus
  - self.log_run() → loggea inicio/fin de cada ejecución del agente

Todos los agentes heredan de esta clase para no repetir infraestructura.

Uso:
    class MyAgent(BaseAgent):
        AGENT_ID = "my_agent"

        async def run(self, input: dict) -> dict:
            await self.log_run("started", input)
            result = await self.llm(
                task_type=TaskType.GENERATION,
                system="Eres un experto en...",
                user_message="Genera...",
            )
            await self.track(Events.AGENT_COMPLETED, properties={"result": result})
            await self.log_run("completed", result)
            return result
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Carga .env antes de cualquier os.getenv() de módulo.
# base.py es el primer módulo del paquete agents/ en importarse
# (via agents/__init__.py), por lo que este load_dotenv garantiza
# que GATEWAY_URL y todas las demás variables estén disponibles
# para todos los submódulos del ecosistema.
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx
import structlog

from cdp.events import Events

log = structlog.get_logger(__name__)

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")


class BaseAgent:
    """
    Clase base para todos los agentes del AI Growth Engine.

    Parámetros:
        cdp:   instancia de CDPClient (opcional — si no se pasa, el tracking se omite)
        bus:   instancia de EventBus (opcional — si no se pasa, no se publica en Bus)
    """

    AGENT_ID: str = "base_agent"  # Override en cada subclase

    def __init__(self, cdp=None, bus=None):
        self._cdp = cdp
        self._bus = bus
        self._http: httpx.AsyncClient | None = None
        self.log = structlog.get_logger(self.AGENT_ID)

    # ── HTTP client lifecycle ────────────────────────────────────────────────
    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ── LLM ────────────────────────────────────────────────────────────────
    async def llm(
        self,
        task_type: str,
        system: str,
        user_message: str,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        conversation_history: list[dict] | None = None,
    ) -> str:
        """
        Llama al LLM Gateway y retorna el texto de respuesta.

        Args:
            task_type:  'generation' | 'reasoning' | 'classification' | 'extraction' | 'conversation'
            system:     system prompt del agente
            user_message: el mensaje del usuario / la tarea a ejecutar
            max_tokens: límite de tokens en la respuesta
            temperature: 0.0 = determinista, 1.0 = creativo
            conversation_history: lista de mensajes previos [{"role": "...", "content": "..."}]

        Returns:
            Texto plano de la respuesta del LLM
        """
        messages = conversation_history or []
        messages = [*messages, {"role": "user", "content": user_message}]

        payload = {
            "agent_id": self.AGENT_ID,
            "task_type": task_type,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        start = time.time()
        http = await self._get_http()

        try:
            response = await http.post("/v1/complete", json=payload)
            response.raise_for_status()
            data = response.json()
            latency = int((time.time() - start) * 1000)

            self.log.debug(
                "llm.call",
                task_type=task_type,
                tokens=data["usage"]["total_tokens"],
                cost_usd=data["usage"]["cost_usd"],
                latency_ms=latency,
            )
            return data["content"]

        except httpx.HTTPStatusError as e:
            self.log.error("llm.error", status=e.response.status_code, body=e.response.text)
            raise
        except httpx.ConnectError:
            self.log.error(
                "llm.gateway_unreachable",
                gateway=GATEWAY_URL,
                hint="¿Está corriendo el LLM Gateway? Ejecuta: make gateway-dev",
            )
            raise

    # ── CDP Event Tracking ───────────────────────────────────────────────────
    async def track(
        self,
        event_type: str,
        user_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Registra un evento en el CDP. No falla si el CDP no está disponible."""
        if not self._cdp:
            self.log.debug("track.skipped", reason="no_cdp", event_type=event_type)
            return
        try:
            await self._cdp.track(
                event_type=event_type,
                user_id=user_id,
                agent_id=self.AGENT_ID,
                properties=properties,
            )
        except Exception as e:
            self.log.warning("track.failed", error=str(e), event_type=event_type)

    # ── Bus Publish ──────────────────────────────────────────────────────────
    async def publish(
        self,
        channel: str,
        event: str,
        data: dict[str, Any],
    ) -> None:
        """Publica un mensaje en el Event Bus. No falla si el Bus no está disponible."""
        if not self._bus:
            return
        try:
            await self._bus.publish(channel, event, data, agent_id=self.AGENT_ID)
        except Exception as e:
            self.log.warning("publish.failed", error=str(e), event=event)

    # ── Run logging ──────────────────────────────────────────────────────────
    async def log_run(
        self,
        status: str,
        data: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> None:
        """Loggea el inicio y fin de una ejecución del agente en el CDP."""
        await self.track(
            event_type=Events.AGENT_TRIGGERED if status == "started" else Events.AGENT_COMPLETED,
            user_id=user_id,
            properties={"status": status, **(data or {})},
        )
        self.log.info(f"agent.{status}", agent_id=self.AGENT_ID, user_id=user_id)
