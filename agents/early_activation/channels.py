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


# ── HTML Email Template ───────────────────────────────────────────────────────
def _build_html_email(subject: str, body: str, to_email: str) -> str:
    """
    Construye un email HTML responsivo con la marca de elempleo.com.
    Convierte el texto plano del agente en un email estructurado con:
    - Header con logo elempleo
    - Cuerpo formateado con párrafos
    - Botón CTA
    - Footer con enlace de cancelar suscripción
    """
    # Convertir saltos de línea en párrafos HTML
    paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
    body_html = "".join(
        f'<p style="margin:0 0 16px 0;color:#333333;font-size:15px;line-height:1.7;">'
        f'{p.replace(chr(10), "<br>")}</p>'
        for p in paragraphs
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#f2f2f2;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f2f2f2;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;">

          <!-- ── Header ── -->
          <tr>
            <td style="background:#e8411e;border-radius:8px 8px 0 0;
                        padding:24px 40px;text-align:center;">
              <span style="color:#ffffff;font-size:26px;font-weight:bold;
                           letter-spacing:-0.5px;text-decoration:none;">
                elempleo<span style="color:#ffd200;">.com</span>
              </span>
            </td>
          </tr>

          <!-- ── Cuerpo ── -->
          <tr>
            <td style="background:#ffffff;padding:36px 40px 24px 40px;">
              {body_html}
            </td>
          </tr>

          <!-- ── CTA Button ── -->
          <tr>
            <td style="background:#ffffff;padding:0 40px 36px 40px;
                        text-align:center;">
              <a href="https://www.elempleo.com"
                 style="display:inline-block;background:#e8411e;color:#ffffff;
                        font-size:15px;font-weight:bold;text-decoration:none;
                        padding:14px 36px;border-radius:6px;
                        letter-spacing:0.3px;">
                Ver vacantes →
              </a>
            </td>
          </tr>

          <!-- ── Divider ── -->
          <tr>
            <td style="background:#ffffff;padding:0 40px;">
              <hr style="border:none;border-top:1px solid #eeeeee;margin:0;">
            </td>
          </tr>

          <!-- ── Footer ── -->
          <tr>
            <td style="background:#fafafa;border-radius:0 0 8px 8px;
                        padding:20px 40px;text-align:center;">
              <p style="margin:0 0 6px 0;color:#aaaaaa;font-size:12px;
                         line-height:1.6;">
                Recibiste este correo en
                <span style="color:#555555;">{to_email}</span>
                porque tienes una cuenta en elempleo.com.
              </p>
              <p style="margin:0;font-size:12px;">
                <a href="https://www.elempleo.com/unsuscribe"
                   style="color:#e8411e;text-decoration:none;">
                  Cancelar suscripción
                </a>
                &nbsp;·&nbsp;
                <a href="https://www.elempleo.com"
                   style="color:#e8411e;text-decoration:none;">
                  elempleo.com
                </a>
                &nbsp;·&nbsp;
                <a href="https://www.elempleo.com/politica-privacidad"
                   style="color:#e8411e;text-decoration:none;">
                  Política de privacidad
                </a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

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

        html_body = _build_html_email(subject=subject, body=body, to_email=to)

        payload = {
            "from": {"email": "noreply@elempleo.com", "name": from_name},
            "to":   [{"email": to}],
            "subject": subject,
            "text": body,        # Fallback para clientes sin soporte HTML
            "html": html_body,   # Versión HTML completa
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
