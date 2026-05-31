"""
Claude API Client con routing inteligente y tracking de costos.
──────────────────────────────────────────────────────────────
- Tareas simples (clasificación/extracción) → claude-haiku  (barato)
- Tareas complejas (generación/razonamiento) → claude-sonnet (potente)
- Retries automáticos con backoff exponencial
- Logging de cada llamada con costo estimado
"""

from __future__ import annotations

import time
from typing import AsyncIterator

import anthropic
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from models import CompletionRequest, CompletionResponse, TaskType, UsageStats

log = structlog.get_logger(__name__)

# ── Routing de modelos ────────────────────────────────────────────────────────
MODEL_ROUTING: dict[TaskType, str] = {
    TaskType.GENERATION:     "claude-sonnet-4-6",
    TaskType.REASONING:      "claude-sonnet-4-6",
    TaskType.CONVERSATION:   "claude-sonnet-4-6",
    TaskType.CLASSIFICATION: "claude-haiku-4-5-20251001",
    TaskType.EXTRACTION:     "claude-haiku-4-5-20251001",
}

# ── Precios por millón de tokens (USD) ────────────────────────────────────────
MODEL_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.25,
        "output": 1.25,
    },
}


class ClaudeClient:
    """
    Wrapper sobre el Anthropic SDK con:
    - Model routing automático por task_type
    - Retries con backoff exponencial
    - Tracking de tokens y costo por llamada
    """

    def __init__(self, api_key: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        log.info("claude_client.initialized")

    @retry(
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
    )
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """
        Ejecuta una completion con Claude.
        Routea al modelo correcto según task_type.
        """
        model = MODEL_ROUTING[request.task_type]
        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        kwargs: dict = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.system:
            kwargs["system"] = request.system

        start_ms = time.time()

        log.debug(
            "claude.request",
            agent_id=request.agent_id,
            model=model,
            task_type=request.task_type,
            messages_count=len(messages),
        )

        response = await self._client.messages.create(**kwargs)

        latency_ms = int((time.time() - start_ms) * 1000)
        content = response.content[0].text

        # Calcular costo
        prices = MODEL_PRICES.get(model, MODEL_PRICES["claude-sonnet-4-6"])
        cost_usd = (
            response.usage.input_tokens * prices["input"]
            + response.usage.output_tokens * prices["output"]
        ) / 1_000_000

        usage = UsageStats(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            cost_usd=round(cost_usd, 6),
            model_used=model,
            latency_ms=latency_ms,
        )

        log.info(
            "claude.response",
            agent_id=request.agent_id,
            model=model,
            tokens_total=usage.total_tokens,
            cost_usd=usage.cost_usd,
            latency_ms=latency_ms,
        )

        return CompletionResponse(
            content=content,
            usage=usage,
            agent_id=request.agent_id,
            task_type=request.task_type.value,
        )

    async def ping(self) -> bool:
        """Verifica que la API key sea válida con una llamada mínima."""
        try:
            await self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": "Responde solo: ok"}],
            )
            return True
        except Exception as e:
            log.error("claude.ping_failed", error=str(e))
            return False
