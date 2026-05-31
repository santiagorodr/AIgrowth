"""
Churn Predictor Agent
──────────────────────────────────────────────────────────────────────────
Detecta usuarios en riesgo de abandono antes de que se vayan.

Flujo por usuario:
  1. Obtiene historial de eventos del CDP (últimos 20 eventos)
  2. Calcula días de inactividad desde last_active_at
  3. Llama a Claude Haiku para clasificar riesgo (high/medium/low)
  4. Escribe evento `churn.risk_detected` al CDP
  5. Retorna ChurnAnalysis con riesgo, razón y acción recomendada

Uso:
    agent = ChurnPredictorAgent(cdp=cdp)
    result = await agent.analyze_batch(days_inactive=7)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from agents.base import BaseAgent
from agents.churn_predictor.models import BatchResult, ChurnAnalysis, RiskLevel
from agents.churn_predictor.prompts import CHURN_SYSTEM, churn_user_message
from cdp.events import Events

log = structlog.get_logger(__name__)

# Riesgo por defecto si el LLM falla o retorna JSON inválido
_FALLBACK_RISK = RiskLevel.MEDIUM


class ChurnPredictorAgent(BaseAgent):
    """
    Agente que clasifica el riesgo de churn de usuarios inactivos.

    Parámetros:
        cdp:  CDPClient (opcional — sin CDP, no trackea eventos ni lee historial)
        bus:  EventBus stub (ignorado en Fase 2 — polling sobre PostgreSQL)
    """

    AGENT_ID = "churn_predictor"

    # ── Análisis individual ────────────────────────────────────────────────────

    async def analyze_user(self, user: dict) -> ChurnAnalysis:
        """
        Analiza el riesgo de churn de un único usuario.

        Args:
            user: dict con campos de la tabla `users` (id, full_name, last_active_at, etc.)

        Returns:
            ChurnAnalysis con nivel de riesgo, razón y acción recomendada.
        """
        user_id = str(user.get("id", ""))
        await self.log_run("started", user_id=user_id)

        # 1. Enriquecer con historial de eventos
        context = await self._get_user_context(user)

        # 2. Clasificar con Claude Haiku
        analysis = await self._classify(user, context)

        # 3. Trackear evento en CDP
        await self.track(
            event_type=Events.CHURN_RISK_DETECTED,
            user_id=user_id,
            properties={
                "risk_level":         analysis.risk_level,
                "risk_score":         analysis.risk_score,
                "risk_reason":        analysis.risk_reason,
                "key_signals":        analysis.key_signals,
                "recommended_action": analysis.recommended_action,
                "days_inactive":      analysis.days_inactive,
            },
        )

        await self.log_run("completed", user_id=user_id)
        return analysis

    # ── Análisis batch ─────────────────────────────────────────────────────────

    async def analyze_batch(self, days_inactive: int = 7) -> BatchResult:
        """
        Analiza todos los usuarios inactivos en los últimos N días.

        Args:
            days_inactive: umbral de inactividad (default: 7 días)

        Returns:
            BatchResult con resumen y lista de análisis individuales.
        """
        # Obtener usuarios inactivos desde el CDP
        if self._cdp:
            users = await self._cdp.get_inactive_users(days_inactive=days_inactive)
        else:
            users = []
            self.log.warning("churn.no_cdp", msg="Sin CDP — retornando batch vacío")

        if not users:
            self.log.info("churn.batch_empty", days_inactive=days_inactive)
            return BatchResult.from_analyses([])

        self.log.info("churn.batch_start", users=len(users), days_inactive=days_inactive)

        start = time.time()
        analyses = []

        for user in users:
            try:
                analysis = await self.analyze_user(dict(user))
                analyses.append(analysis)
            except Exception as exc:
                self.log.error(
                    "churn.user_error",
                    user_id=str(user.get("id")),
                    error=str(exc),
                )

        result = BatchResult.from_analyses(analyses)
        result.duration_seconds = round(time.time() - start, 2)

        self.log.info(
            "churn.batch_done",
            total=result.total_analyzed,
            high=result.high_risk,
            medium=result.medium_risk,
            low=result.low_risk,
            duration_s=result.duration_seconds,
        )
        return result

    # ── Privados ───────────────────────────────────────────────────────────────

    async def _get_user_context(self, user: dict) -> dict[str, Any]:
        """Obtiene los últimos eventos del usuario desde el CDP."""
        events: list[dict] = []
        if self._cdp:
            try:
                events = await self._cdp.get_user_events(
                    str(user.get("id", "")), limit=20
                )
            except Exception as exc:
                self.log.warning("churn.events_error", error=str(exc))

        return {"events": events}

    async def _classify(self, user: dict, context: dict) -> ChurnAnalysis:
        """Llama a Claude Haiku para clasificar el riesgo de churn."""
        last_active = user.get("last_active_at")
        if last_active:
            if isinstance(last_active, str):
                last_active = datetime.fromisoformat(last_active)
            # Asegurar timezone-aware
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            days_inactive = (datetime.now(timezone.utc) - last_active).days
        else:
            last_active = datetime.now(timezone.utc)
            days_inactive = 0

        events = context.get("events", [])

        raw = await self.llm(
            task_type="classification",
            system=CHURN_SYSTEM,
            user_message=churn_user_message(user, events, days_inactive),
            max_tokens=300,
            temperature=0.0,  # determinista — clasificación
        )

        return self._parse_llm_response(raw, user, last_active, days_inactive)

    def _parse_llm_response(
        self,
        raw: str,
        user: dict,
        last_active_at: datetime,
        days_inactive: int,
    ) -> ChurnAnalysis:
        """
        Parsea la respuesta JSON de Haiku.
        Si el JSON es inválido, retorna un análisis con riesgo MEDIUM como fallback.
        """
        try:
            # Extraer JSON (a veces Haiku incluye texto antes/después)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No se encontró JSON en la respuesta")

            data = json.loads(raw[start:end])

            return ChurnAnalysis(
                user_id=str(user.get("id", "")),
                full_name=user.get("full_name", ""),
                email=user.get("email"),
                risk_level=RiskLevel(data.get("risk_level", _FALLBACK_RISK)),
                risk_score=float(data.get("risk_score", 0.5)),
                risk_reason=data.get("risk_reason", "No se pudo determinar la razón"),
                key_signals=data.get("key_signals", []),
                recommended_action=data.get("recommended_action", "monitor"),
                days_inactive=days_inactive,
                last_active_at=last_active_at,
            )

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            self.log.warning(
                "churn.parse_error",
                error=str(exc),
                raw=raw[:200],
            )
            # Fallback basado en días de inactividad
            if days_inactive > 21:
                fallback_level, fallback_score = RiskLevel.HIGH, 0.75
            elif days_inactive > 14:
                fallback_level, fallback_score = RiskLevel.MEDIUM, 0.50
            else:
                fallback_level, fallback_score = RiskLevel.LOW, 0.25

            return ChurnAnalysis(
                user_id=str(user.get("id", "")),
                full_name=user.get("full_name", ""),
                email=user.get("email"),
                risk_level=fallback_level,
                risk_score=fallback_score,
                risk_reason=f"Clasificación automática — {days_inactive} días sin actividad",
                key_signals=[f"{days_inactive} días de inactividad"],
                recommended_action="monitor",
                days_inactive=days_inactive,
                last_active_at=last_active_at,
            )
