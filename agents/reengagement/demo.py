"""
Re-engagement Agent — Demo CLI
────────────────────────────────
Muestra mensajes de reactivación generados para usuarios en riesgo.

Uso:
    python -m agents.reengagement.demo              # procesa pendientes reales
    python -m agents.reengagement.demo --no-llm     # simula sin llamar a Claude
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

load_dotenv(Path(__file__).parent.parent.parent / ".env")

console = Console()
app     = typer.Typer(add_completion=False)


_MOCK_RESULTS = [
    {
        "full_name":       "Ana García",
        "risk_level":      "high",
        "channel":         "log",
        "success":         True,
        "message_id":      "log-a1b2c3d4",
        "subject_preview": "Ana, hay vacantes de Backend esperándote 💼",
    },
    {
        "full_name":       "Miguel Torres",
        "risk_level":      "high",
        "channel":         "log",
        "success":         True,
        "message_id":      "log-e5f6g7h8",
        "subject_preview": "Miguel, te echamos de menos en elempleo",
    },
    {
        "full_name":       "Carlos Mora",
        "risk_level":      "medium",
        "channel":         "log",
        "success":         True,
        "message_id":      "log-i9j0k1l2",
        "subject_preview": "Nuevas oportunidades de Finanzas en Medellín",
    },
]

_MOCK_MESSAGES = {
    "Ana García": {
        "subject":    "Ana, hay vacantes de Backend esperándote 💼",
        "body":       (
            "Hola Ana,\n\n"
            "Hace un tiempo que no te vemos por elempleo.com y queremos saber cómo estás.\n\n"
            "Tenemos 3 vacantes nuevas de Desarrolladora Backend Python en Bogotá que "
            "coinciden perfectamente con tu perfil. Empresas como Bancolombia y Rappi "
            "están buscando alguien con tus habilidades.\n\n"
            "¿Te animas a echarles un vistazo? Solo toma 5 minutos.\n\n"
            "El equipo de elempleo"
        ),
        "whatsapp":   "Hola Ana 👋 Hay 3 vacantes nuevas de Backend Python que son para ti. ¡Entra a verlas!",
    },
    "Miguel Torres": {
        "subject":    "Miguel, te echamos de menos en elempleo",
        "body":       (
            "Hola Miguel,\n\n"
            "Llevas un tiempo sin visitarnos y queremos reconectarnos.\n\n"
            "En elempleo encontramos algo especial: 5 empresas están buscando "
            "perfiles de Marketing Digital como el tuyo en Bogotá.\n\n"
            "Tu experiencia de 4 años es exactamente lo que buscan. ¿Exploramos juntos?\n\n"
            "El equipo de elempleo"
        ),
        "whatsapp":   "Hola Miguel 👋 5 empresas buscan tu perfil de Marketing Digital. ¡No te pierdas estas oportunidades!",
    },
    "Carlos Mora": {
        "subject":    "Nuevas oportunidades de Finanzas en Medellín",
        "body":       (
            "Hola Carlos,\n\n"
            "Te escribimos porque esta semana llegaron nuevas vacantes de Analista "
            "Financiero en Medellín que encajan con tu perfil.\n\n"
            "Con tu formación en Contaduría, tienes muy buenas posibilidades. "
            "¿Le damos una mirada?\n\n"
            "El equipo de elempleo"
        ),
        "whatsapp":   "Hola Carlos 👋 Hay vacantes nuevas de Finanzas en Medellín para ti. ¡Échales un vistazo!",
    },
}


async def _run_demo(no_llm: bool) -> None:
    console.rule("[bold blue]📧 Re-engagement Agent — Elempleo AI Growth Engine")
    console.print()

    if no_llm:
        console.print(Panel(
            "[yellow]Modo offline — sin llamadas a Claude Sonnet[/]\n"
            "Los mensajes son simulados para demo.",
            title="[bold yellow]⚡ Demo Offline[/]",
            border_style="yellow",
        ))
        console.print()
        _render_results(_MOCK_RESULTS, _MOCK_MESSAGES, cost=0.0)
        return

    # ── Modo real ─────────────────────────────────────────────────────────────
    import asyncpg
    from agents.reengagement.agent import ReengagementAgent
    from cdp.events import CDPClient

    pg_url = os.getenv("POSTGRES_URL", "")
    if not pg_url:
        console.print("[red]❌ POSTGRES_URL no configurada en .env[/]")
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("[cyan]Conectando...[/]")) as p:
        t = p.add_task("connect")
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
        cdp  = CDPClient(postgres_url=pg_url)
        await cdp.connect()
        p.update(t, completed=True)

    agent = ReengagementAgent(cdp=cdp, pool=pool)

    # Verificar cuántos usuarios están pendientes
    pending = await agent._get_pending_users(limit=20)

    if not pending:
        console.print(
            "\n[green]✅ No hay usuarios pendientes de reactivación.[/]\n"
            "[dim]Tip: Corre primero [bold]make demo-churn[/bold] para generar eventos churn.risk_detected[/]"
        )
        await agent.close()
        await cdp.close()
        await pool.close()
        return

    console.print(f"[cyan]Usuarios pendientes de reactivación: {len(pending)}[/]\n")

    raw_results = []
    msg_previews: dict[str, dict] = {}

    with Progress(SpinnerColumn(), TextColumn("[cyan]Generando mensajes con Claude Sonnet...[/]")) as progress:
        task = progress.add_task("generate", total=len(pending))

        for row in pending:
            import json as _json
            user_id    = str(row["user_id"])
            churn_data = row.get("properties") or {}
            if isinstance(churn_data, str):
                try:
                    churn_data = _json.loads(churn_data)
                except Exception:
                    churn_data = {}

            try:
                result = await agent.process_user(user_id=user_id, churn_data=churn_data)
                raw_results.append({
                    "full_name":       result.full_name,
                    "risk_level":      result.risk_level,
                    "channel":         result.channel,
                    "success":         result.success,
                    "message_id":      result.message_id,
                    "subject_preview": result.subject_preview,
                })
            except Exception as exc:
                raw_results.append({
                    "full_name":       user_id[:8],
                    "risk_level":      churn_data.get("risk_level", "?"),
                    "channel":         "log",
                    "success":         False,
                    "message_id":      None,
                    "subject_preview": f"Error: {str(exc)[:50]}",
                })
            progress.advance(task)

    await agent.close()
    await cdp.close()
    await pool.close()

    _render_results(raw_results, msg_previews, cost=len(raw_results) * 0.010)


def _render_results(results: list[dict], msg_previews: dict, cost: float) -> None:
    if not results:
        console.print("[green]✅ Sin mensajes enviados.[/]")
        return

    # Tabla de resultados
    table = Table(title="Mensajes de reactivación enviados", box=box.ROUNDED, show_lines=True)
    table.add_column("Nombre", style="white")
    table.add_column("Riesgo", justify="center", width=10)
    table.add_column("Canal", justify="center", width=10)
    table.add_column("Asunto / Preview", style="dim")
    table.add_column("Estado", justify="center", width=8)

    risk_emoji = {"high": "🔴 HIGH", "medium": "🟡 MED"}
    ok_sent    = 0

    for r in results:
        risk  = r["risk_level"]
        state = "[green]✅[/]" if r["success"] else "[red]❌[/]"
        if r["success"]:
            ok_sent += 1

        table.add_row(
            r["full_name"],
            risk_emoji.get(risk, risk.upper()),
            r["channel"].upper(),
            r["subject_preview"] or "[dim]—[/]",
            state,
        )

    console.print(table)
    console.print()

    # Mostrar preview de mensaje mock si existe
    for name, msg in list(msg_previews.items())[:2]:
        console.print(Panel(
            f"[bold]{msg['subject']}[/]\n\n{msg['body'][:300]}...\n\n"
            f"[dim]WhatsApp: {msg['whatsapp']}[/]",
            title=f"[bold cyan]📧 Mensaje para {name}[/]",
            border_style="cyan",
        ))

    # Resumen
    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column(width=22)
    summary.add_column()
    summary.add_row("Total procesados:", str(len(results)))
    summary.add_row("[green]✅ Enviados:[/]",    f"[green]{ok_sent}[/]")
    summary.add_row("[red]❌ Fallidos:[/]",       f"[red]{len(results) - ok_sent}[/]")
    if cost > 0:
        summary.add_row("Costo estimado:",        f"~${cost:.4f} USD")

    console.print(Panel(summary, title="[bold]📊 Resumen[/]", border_style="blue"))
    console.print()
    console.print(
        "[dim]Eventos [bold]reengagement.message_sent[/] registrados en el CDP.[/]"
    )


@app.command()
def main(
    no_llm: bool = typer.Option(False, "--no-llm", help="Demo sin llamar a Claude"),
) -> None:
    """Demo del Re-engagement Agent — genera mensajes personalizados de reactivación."""
    asyncio.run(_run_demo(no_llm=no_llm))


if __name__ == "__main__":
    app()
