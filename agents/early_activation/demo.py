"""
Demo interactiva del Early Activation Agent
──────────────────────────────────────────────────────────────────────
Simula el registro de un nuevo usuario y ejecuta los 5 pasos de la
secuencia de 72 horas en tiempo acelerado, mostrando cada mensaje
generado por Claude en la terminal con formato Rich.

Uso:
    python -m agents.early_activation.demo
    python -m agents.early_activation.demo --user 3
    python -m agents.early_activation.demo --custom

Flags:
    --user N    → Usa el usuario N del archivo mock_users.json (1-20)
    --custom    → Permite ingresar datos del usuario manualmente
    --no-llm    → Modo offline: usa mensajes mock sin llamar a Claude
    --delay N   → Segundos entre pasos (default 1.5)
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from .agent import EarlyActivationAgent
from .models import ActivationEvent, Channel, StepKey
from .sequences import SEQUENCE

console = Console()
app     = typer.Typer(add_completion=False)

# Colores por canal
CHANNEL_COLORS = {
    Channel.EMAIL:    "blue",
    Channel.WHATSAPP: "green",
    Channel.PUSH:     "yellow",
    Channel.LOG:      "dim",
}

STEP_EMOJIS = {
    StepKey.WELCOME:            "🚀",
    StepKey.CV_TIP:             "💡",
    StepKey.EMPLOYER_SIGNAL:    "📢",
    StepKey.FIRST_APPLY_NUDGE:  "🎯",
    StepKey.REACTIVATION_CHECK: "🤝",
}


def _load_mock_user(index: int) -> dict[str, Any]:
    path = Path(__file__).parent.parent.parent / "data" / "mock_users.json"
    with open(path) as f:
        users = json.load(f)
    return users[(index - 1) % len(users)]


def _build_event(user: dict) -> ActivationEvent:
    return ActivationEvent(
        user_id=user.get("id", f"demo-{random.randint(1000,9999)}"),
        full_name=user.get("full_name", "Usuario Demo"),
        email=user.get("email", "demo@test.com"),
        phone=user.get("phone", "+573001234567"),
        source=user.get("source", "organic"),
        city=user.get("city", "Bogotá"),
        current_title=user.get("current_title", "Profesional"),
        skills=user.get("skills", []),
        experience_years=user.get("experience_years", 2),
        registered_at=datetime.now(timezone.utc),
    )


def _print_user_panel(event: ActivationEvent) -> None:
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    table.add_column("Key",   style="dim")
    table.add_column("Value", style="bold")

    table.add_row("ID",         event.user_id[:16] + "...")
    table.add_row("Nombre",     event.full_name)
    table.add_row("Cargo",      event.current_title or "—")
    table.add_row("Ciudad",     event.city or "—")
    table.add_row("Skills",     ", ".join(event.skills[:4]) or "—")
    table.add_row("Canal",      f"📧 {event.email or '—'}  📱 {event.phone or '—'}")
    table.add_row("Fuente",     event.source)

    console.print(Panel(table, title="[bold cyan]👤 Nuevo Usuario Registrado[/]", border_style="cyan"))


def _print_sequence_overview() -> None:
    table = Table(title="Secuencia de Activación — 72 horas", box=box.ROUNDED, border_style="dim")
    table.add_column("Paso",     style="bold", width=6)
    table.add_column("Step",     style="cyan")
    table.add_column("Hora",     style="yellow", justify="right")
    table.add_column("Canal",    style="magenta")
    table.add_column("Condición",style="dim")

    for i, step in enumerate(SEQUENCE, 1):
        emoji  = STEP_EMOJIS.get(step.key, "•")
        canal  = step.channel.value.upper()
        delay  = f"H+{step.delay_hours}"
        cond   = "" if step.condition == "always" else f"[dim]({step.condition})[/dim]"
        table.add_row(str(i), f"{emoji} {step.key.value}", delay, canal, cond)

    console.print(table)
    console.print()


def _print_step_header(step_key: StepKey, step_num: int, delay_hours: int) -> None:
    emoji  = STEP_EMOJIS.get(step_key, "•")
    label  = f"Paso {step_num}/5 — {step_key.value.replace('_', ' ').title()} (H+{delay_hours})"
    console.print(Rule(f"[bold]{emoji}  {label}[/]", style="dim"))


def _print_message_panel(
    step_key: StepKey,
    channel: Channel,
    subject: str,
    body: str,
    whatsapp_text: str,
    success: bool,
) -> None:
    color = CHANNEL_COLORS.get(channel, "white")

    if channel in (Channel.EMAIL, Channel.LOG):
        content = f"[bold]Asunto:[/bold] {subject}\n\n{body}"
    else:
        content = whatsapp_text or body

    status_icon = "✅" if success else "❌"
    title = f"[bold {color}]{status_icon} {channel.value.upper()} — {step_key.value}[/bold {color}]"
    console.print(Panel(content, title=title, border_style=color, padding=(1, 2)))


def _print_summary(results: list[dict]) -> None:
    console.print()
    console.print(Rule("[bold green]🎉 Secuencia completada[/bold green]"))
    console.print()

    table = Table(box=box.SIMPLE_HEAVY, show_header=True)
    table.add_column("Paso",       style="bold")
    table.add_column("Canal",      style="cyan")
    table.add_column("Estado",     justify="center")
    table.add_column("Message ID", style="dim")

    for r in results:
        icon  = "✅" if r["success"] else "❌"
        table.add_row(
            r["step"],
            r["channel"],
            icon,
            (r.get("message_id") or "—")[:20],
        )

    console.print(table)
    sent   = sum(1 for r in results if r["success"])
    failed = len(results) - sent
    console.print(f"\n[green]Enviados:[/green] {sent}   [red]Fallidos:[/red] {failed}")
    console.print()


async def _run_demo(
    event: ActivationEvent,
    use_llm: bool = True,
    delay: float = 1.5,
) -> None:
    """Ejecuta la demo completa de la secuencia."""

    console.print()
    console.print(Panel.fit(
        "[bold magenta]elempleo · Early Activation Agent[/bold magenta]\n"
        "[dim]Secuencia de activación 72h — modo demo[/dim]",
        border_style="magenta",
    ))
    console.print()

    _print_user_panel(event)
    console.print()
    _print_sequence_overview()

    agent   = EarlyActivationAgent()  # Sin DB → modo in-memory
    results = []

    for i, step_conf in enumerate(SEQUENCE, 1):
        _print_step_header(step_conf.key, i, step_conf.delay_hours)
        console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task(
                f"[cyan]Generando mensaje con Claude ({step_conf.channel.value})...[/cyan]"
            )

            if use_llm:
                result = await agent._run_step_in_memory(step_conf.key, event)
            else:
                # Modo offline — mensajes mock sin LLM
                result = _mock_result(step_conf)

            progress.update(task_id, completed=True)

        # Obtener el último mensaje generado para mostrarlo
        # En _run_step_in_memory el mensaje se genera internamente;
        # para la demo lo regeneramos sin envío para capturarlo
        try:
            context   = await agent._build_context(step_conf.key, event)
            generated = await agent._generate_message(step_conf.key, event, context)
            _print_message_panel(
                step_key=step_conf.key,
                channel=result.channel,
                subject=generated.subject,
                body=generated.body,
                whatsapp_text=generated.whatsapp_text,
                success=result.success,
            )
        except Exception:
            console.print(f"[dim](mensaje generado — canal: {result.channel})[/dim]")

        results.append({
            "step":       step_conf.key.value,
            "channel":    result.channel.value,
            "success":    result.success,
            "message_id": result.message_id,
        })

        console.print()
        await asyncio.sleep(delay)

    await agent.close()
    _print_summary(results)


def _mock_result(step_conf) -> Any:
    """Resultado mock para modo --no-llm."""
    from .models import ChannelResult
    import uuid
    return ChannelResult(
        success=True,
        channel=step_conf.channel,
        message_id=f"mock-{uuid.uuid4().hex[:8]}",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command()
def main(
    user:    int   = typer.Option(1,     "--user",   "-u", help="Índice del usuario mock (1-20)"),
    custom:  bool  = typer.Option(False, "--custom", "-c", help="Ingresar datos del usuario manualmente"),
    no_llm:  bool  = typer.Option(False, "--no-llm",       help="Modo offline sin llamar a Claude"),
    delay:   float = typer.Option(1.5,   "--delay",  "-d", help="Segundos entre pasos"),
) -> None:
    """
    Demo interactiva del Early Activation Agent.

    Simula el registro de un nuevo usuario y ejecuta los 5 pasos
    de la secuencia de activación de 72 horas.
    """
    if custom:
        console.print("\n[bold cyan]Ingresa los datos del usuario:[/bold cyan]")
        user_data = {
            "id":              f"custom-{random.randint(1000, 9999)}",
            "full_name":       typer.prompt("Nombre completo"),
            "email":           typer.prompt("Email",           default="test@example.com"),
            "phone":           typer.prompt("Teléfono (e.g. +573001234567)", default="+573001234567"),
            "current_title":   typer.prompt("Cargo actual",    default="Desarrollador"),
            "city":            typer.prompt("Ciudad",          default="Bogotá"),
            "source":          typer.prompt("Fuente (organic/whatsapp/referral)", default="organic"),
            "skills":          typer.prompt("Skills (separados por coma)", default="Python, SQL").split(", "),
            "experience_years": int(typer.prompt("Años de experiencia", default="3")),
        }
    else:
        user_data = _load_mock_user(user)
        console.print(f"[dim]Usando usuario #{user} del archivo mock_users.json[/dim]")

    event = _build_event(user_data)
    asyncio.run(_run_demo(event, use_llm=not no_llm, delay=delay))


if __name__ == "__main__":
    app()
