"""
Adaptadores de canales del Early Activation Agent
───────────────────────────────────────────────────
Cada canal implementa send() y retorna un ChannelResult.

Canales disponibles:
  EmailChannel     — Mailtrap sandbox (POC) o SendGrid (producción)
  WhatsAppChannel  — Meta Business API Sandbox
  LogChannel       — Fallback: imprime en consola, siempre éxito

Uso:
    channel = EmailChannel()
    result  = await channel.send(
        to="usuario@email.com",
        subject="Bienvenido",
        body="Hola...",
    )

Comportamiento en POC:
  - Si las credenciales NO están configuradas → usa LogChannel automáticamente
  - Si las credenciales SÍ están → envía de verdad al sandbox
"""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod

import httpx
import structlog

from .models import Channel, ChannelResult

log = structlog.get_logger(__name__)

# ── Credenciales desde .env ────────────────────────────────────────────────────
MAILTRAP_TOKEN      = os.getenv("MAILTRAP_TOKEN", "")
MAILTRAP_INBOX_ID   = os.getenv("MAILTRAP_INBOX_ID", "")
WHATSAPP_TOKEN      = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID   = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


# ── Interfaz base ─────────────────────────────────────────────────────────────
class BaseChannel(ABC):
    @abstractmethod
    async def send(self, **kwargs) -> ChannelResult:
        ...

    @property
    @abstractmethod
    def channel_type(self) -> Channel:
        ...

    def is_configured(self) -> bool:
        return True


# ── Log Channel (fallback para POC sin credenciales) ──────────────────────────
class LogChannel(BaseChannel):
    """
    Canal de fallback para el POC.
    No envía nada real — imprime el mensaje en el log con formato claro.
    Siempre retorna éxito, así el agente puede correr sin credenciales.
    """

    channel_type = Channel.LOG

    async def send(self, to: str = "", subject: str = "",
                   body: str = "", **kwargs) -> ChannelResult:
        msg_id = f"log-{uuid.uuid4().hex[:8]}"
        log.info(
            "channel.log.sent",
            to=to,
            subject=subject,
            body_preview=body[:120] + ("..." if len(body) > 120 else ""),
            message_id=msg_id,
        )
        # Print formateado para la demo
        print(f"\n{'─'*55}")
        print(f"  📬 [LOG CHANNEL]  Para: {to}")
        print(f"  📌 Asunto: {subject}")
        print(f"  📝 {body[:200]}{'...' if len(body) > 200 else ''}")
        print(f"{'─'*55}\n")
        return ChannelResult(success=True, channel=Channel.LOG, message_id=msg_id)


# ── Email Channel (Mailtrap sandbox) ─────────────────────────────────────────
class EmailChannel(BaseChannel):
    """
    Envía emails via Mailtrap.io (sandbox gratuito para testing).
    Si MAILTRAP_TOKEN no está configurado → delega a LogChannel.

    Mailtrap captura los emails en una bandeja de entrada falsa:
    https://mailtrap.io/inboxes → puedes ver todos los emails enviados.
    """

    channel_type = Channel.EMAIL

    def is_configured(self) -> bool:
        return bool(MAILTRAP_TOKEN and MAILTRAP_INBOX_ID)

    async def send(self, to: str, subject: str, body: str,
                   from_name: str = "elempleo", **kwargs) -> ChannelResult:
        if not self.is_configured():
            log.warning("email.not_configured", hint="Agrega MAILTRAP_TOKEN en .env para enviar emails reales")
            return await LogChannel().send(to=to, subject=subject, body=body)

        payload = {
            "from": {"email": "noreply@elempleo.com", "name": from_name},
            "to":   [{"email": to}],
            "subject": subject,
            "text": body,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://sandbox.api.mailtrap.io/api/send/{MAILTRAP_INBOX_ID}",
                    json=payload,
                    headers={"Authorization": f"Bearer {MAILTRAP_TOKEN}"},
                )
                r.raise_for_status()
                data = r.json()
                msg_id = data.get("message_ids", [None])[0] or uuid.uuid4().hex
                log.info("email.sent", to=to, subject=subject, message_id=msg_id)
                return ChannelResult(success=True, channel=Channel.EMAIL, message_id=msg_id)

        except httpx.HTTPStatusError as e:
            log.error("email.send_error", status=e.response.status_code, body=e.response.text[:200])
            return ChannelResult(success=False, channel=Channel.EMAIL, error=str(e))
        except Exception as e:
            log.error("email.send_error", error=str(e))
            return ChannelResult(success=False, channel=Channel.EMAIL, error=str(e))


# ── WhatsApp Channel (Meta Business API Sandbox) ──────────────────────────────
class WhatsAppChannel(BaseChannel):
    """
    Envía mensajes via WhatsApp Business API (Meta).
    En modo sandbox solo puedes enviar a números que hayan aceptado la invitación.

    Setup sandbox:
    1. Ir a developers.facebook.com → Crear app → WhatsApp
    2. En "Sandbox" → añadir tu número personal
    3. Copiar WHATSAPP_TOKEN y WHATSAPP_PHONE_NUMBER_ID al .env

    Si no está configurado → delega a LogChannel.
    """

    channel_type = Channel.WHATSAPP
    API_URL = "https://graph.facebook.com/v19.0/{phone_id}/messages"

    def is_configured(self) -> bool:
        return bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID)

    async def send(self, to: str, body: str, subject: str = "", **kwargs) -> ChannelResult:
        if not self.is_configured():
            log.warning("whatsapp.not_configured", hint="Agrega WHATSAPP_TOKEN en .env para enviar mensajes WA reales")
            return await LogChannel().send(to=to, subject=subject, body=body)

        # Asegurar formato internacional (+57...)
        phone = to.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = f"+{phone}"
        phone = phone.lstrip("+")  # La API de Meta requiere sin el +

        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": body},
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    self.API_URL.format(phone_id=WHATSAPP_PHONE_ID),
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                        "Content-Type": "application/json",
                    },
                )
                r.raise_for_status()
                data = r.json()
                msg_id = data.get("messages", [{}])[0].get("id", uuid.uuid4().hex)
                log.info("whatsapp.sent", to=phone, message_id=msg_id)
                return ChannelResult(success=True, channel=Channel.WHATSAPP, message_id=msg_id)

        except httpx.HTTPStatusError as e:
            log.error("whatsapp.send_error", status=e.response.status_code, body=e.response.text[:200])
            return ChannelResult(success=False, channel=Channel.WHATSAPP, error=str(e))
        except Exception as e:
            log.error("whatsapp.send_error", error=str(e))
            return ChannelResult(success=False, channel=Channel.WHATSAPP, error=str(e))


# ── Factory: retorna el canal correcto con fallback automático ────────────────
def get_channel(channel: Channel) -> BaseChannel:
    """
    Retorna la instancia del canal solicitado.
    Si el canal no está configurado (sin credenciales) → LogChannel.
    """
    mapping: dict[Channel, BaseChannel] = {
        Channel.EMAIL:    EmailChannel(),
        Channel.WHATSAPP: WhatsAppChannel(),
        Channel.PUSH:     LogChannel(),   # Push no implementado en POC
        Channel.LOG:      LogChannel(),
    }
    instance = mapping.get(channel, LogChannel())
    if not instance.is_configured():
        return LogChannel()
    return instance
