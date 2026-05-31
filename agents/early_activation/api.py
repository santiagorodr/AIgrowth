"""
FastAPI router del Early Activation Agent
──────────────────────────────────────────────────────────────────────
Endpoints:
  POST /agents/early-activation/trigger
    → Inicia la secuencia de 72h para un usuario recién registrado.

  GET  /agents/early-activation/status/{user_id}
    → Retorna el estado actual de la secuencia del usuario.

  POST /agents/early-activation/step
    → Ejecuta un paso específico manualmente (útil para testing/admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .agent import EarlyActivationAgent
from .models import ActivationEvent, SequenceStatus, StepKey

router = APIRouter(prefix="/agents/early-activation", tags=["Early Activation"])


# ── Dependency: instancia del agente ─────────────────────────────────────────
# En producción se inyecta el pool y bus reales vía FastAPI dependencies.
# Para el POC se crea una instancia sin DB (modo LogChannel).
def get_agent() -> EarlyActivationAgent:
    return EarlyActivationAgent()


# ── Request / Response models ─────────────────────────────────────────────────
class TriggerRequest(BaseModel):
    user_id:          str
    full_name:        str
    email:            str | None = None
    phone:            str | None = None
    source:           str = "organic"
    city:             str = ""
    current_title:    str = ""
    skills:           list[str] = []
    experience_years: int = 0


class TriggerResponse(BaseModel):
    message: str
    status:  SequenceStatus


class StepExecuteRequest(BaseModel):
    user_id: str
    step:    StepKey
    # Datos del usuario para generar el mensaje (si no hay DB)
    full_name:     str = ""
    email:         str | None = None
    phone:         str | None = None
    city:          str = ""
    current_title: str = ""
    skills:        list[str] = []


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/trigger", response_model=TriggerResponse, summary="Iniciar secuencia de activación")
async def trigger_sequence(
    req: TriggerRequest,
    agent: EarlyActivationAgent = Depends(get_agent),
) -> TriggerResponse:
    """
    Dispara la secuencia de 72 horas para un nuevo usuario.

    Llama a este endpoint al detectar `user.registered` en el Event Bus,
    o directamente desde el endpoint de registro del backend de elempleo.
    """
    event = ActivationEvent(
        user_id=req.user_id,
        full_name=req.full_name,
        email=req.email,
        phone=req.phone,
        source=req.source,
        city=req.city,
        current_title=req.current_title,
        skills=req.skills,
        experience_years=req.experience_years,
    )

    try:
        status = await agent.trigger(event)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()

    return TriggerResponse(
        message=f"Secuencia de activación iniciada para {req.full_name}",
        status=status,
    )


@router.get(
    "/status/{user_id}",
    response_model=SequenceStatus,
    summary="Estado de la secuencia",
)
async def get_sequence_status(
    user_id: str,
    agent: EarlyActivationAgent = Depends(get_agent),
) -> SequenceStatus:
    """
    Retorna el estado actual de la secuencia de activación del usuario:
    cuántos pasos se enviaron, fallaron, están pendientes y cuál es el próximo.
    """
    try:
        status = await agent.get_status(user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return status


@router.post("/step", summary="Ejecutar paso manualmente (admin/testing)")
async def execute_step_manual(
    req: StepExecuteRequest,
    agent: EarlyActivationAgent = Depends(get_agent),
) -> dict:
    """
    Ejecuta un paso específico de la secuencia manualmente.
    Útil para testing, reenvíos y uso desde el panel de administración.
    """
    from .sequences import SEQUENCE_BY_KEY

    step_conf = SEQUENCE_BY_KEY.get(req.step)
    if not step_conf:
        raise HTTPException(status_code=400, detail=f"Paso desconocido: {req.step}")

    # Construir row sintético (como si viniera de la BD)
    import uuid
    row = {
        "id":           str(uuid.uuid4()),
        "user_id":      req.user_id,
        "step":         req.step.value,
        "channel":      step_conf.channel.value,
        "status":       "pending",
        "metadata":     {"condition": step_conf.condition},
        "scheduled_at": None,
    }

    event = ActivationEvent(
        user_id=req.user_id,
        full_name=req.full_name,
        email=req.email,
        phone=req.phone,
        city=req.city,
        current_title=req.current_title,
        skills=req.skills,
    )

    try:
        result = await agent.execute_step(row, event)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await agent.close()

    return {
        "step":       req.step,
        "success":    result.success,
        "channel":    result.channel,
        "message_id": result.message_id,
        "error":      result.error,
    }
