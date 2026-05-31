"""
Employer Signal Agent — Demo CLI

Uso:
    python -m agents.employer_signal.demo            # simula + procesa (real)
    python -m agents.employer_signal.demo --no-llm   # simulado sin Claude
    python -m agents.employer_signal.demo --n 3      # simular 3 señales
"""
from __future__ import annotations
import asyncio, os, random
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

_MOCK_SIGNALS = [
    {"user": "Andrés García",  "company": "Rappi",       "job": "Desarrollador Backend",  "duration": 87,  "msg": "¡Andrés, Rappi revisó tu perfil hoy! Están buscando un Backend Python y tu experiencia con Django es exactamente lo que necesitan. ¡Este es tu momento!"},
    {"user": "María Rodríguez","company": "Bancolombia", "job": "Analista de Datos",      "duration": 142, "msg": "María, Bancolombia pasó más de 2 minutos revisando tu perfil. Buscan una Contadora con SQL — justo tu perfil. No esperes más, ¡aplica hoy!"},
    {"user": "Carlos Torres",  "company": "Nequi",       "job": "Desarrollador iOS",      "duration": 55,  "msg": "¡Buena noticia Carlos! Nequi vio tu perfil. Están contratando y tu formación en Sistemas es lo que buscan. ¡Dale!"},
    {"user": "Laura Jiménez",  "company": "Grupo Éxito", "job": "Analista de Marketing",  "duration": 73,  "msg": "Laura, el equipo de Grupo Éxito revisó tu perfil. Tu experiencia en marketing digital es exactamente lo que necesitan para su próxima campaña."},
    {"user": "Pedro Ramírez",  "company": "Avianca",     "job": "Analista de Operaciones","duration": 61,  "msg": "¡Pedro! Avianca estuvo mirando tu perfil — buscan alguien con tu background en operaciones. Envíales tu aplicación antes de que cierren la vacante."},
]


async def _run_demo(n: int, no_llm: bool) -> None:
    console.rule("[bold blue]🏢 Employer Signal Agent — Elempleo AI Growth Engine")
    console.print()

    if no_llm:
        console.print(Panel(
            "[yellow]Modo offline — señales y mensajes simulados[/]\n"
            "En producción los eventos vendrían de elempleo.com en tiempo real.",
            title="[bold yellow]⚡ Demo Offline[/]", border_style="yellow",
        ))
        console.print()

        # Mostrar señales simuladas
        signals_table = Table(title=f"📡 {n} señales de empleadores detectadas", box=box.SIMPLE)
        signals_table.add_column("Candidato", style="white")
        signals_table.add_column("Empresa", style="cyan")
        signals_table.add_column("Vacante", style="dim")
        signals_table.add_column("Duración", justify="right", width=10)

        for s in _MOCK_SIGNALS[:n]:
            t = s["duration"]
            signals_table.add_row(
                s["user"], s["company"], s["job"],
                f"[green]{t}s[/]" if t >= 60 else f"[yellow]{t}s[/]",
            )
        console.print(signals_table)
        console.print()

        # Notificaciones generadas
        notif_table = Table(title="📧 Notificaciones generadas", box=box.ROUNDED, show_lines=True)
        notif_table.add_column("Para", style="white", width=16)
        notif_table.add_column("Mensaje", style="dim")
        notif_table.add_column("Estado", justify="center", width=10)

        for s in _MOCK_SIGNALS[:n]:
            notif_table.add_row(s["user"], s["msg"][:80] + "…", "[green]✅ Enviado[/]")
        console.print(notif_table)
        console.print()

        summary = Table(box=box.SIMPLE, show_header=False)
        summary.add_column(width=22); summary.add_column()
        summary.add_row("Señales procesadas:",  str(n))
        summary.add_row("[green]✅ Notificadas:[/]", f"[green]{n}[/]")
        summary.add_row("Costo estimado:",      f"~${n * 0.001:.4f} USD")
        console.print(Panel(summary, title="[bold]📊 Resumen[/]", border_style="blue"))
        return

    # ── Modo real ─────────────────────────────────────────────────────────────
    import asyncpg
    from agents.employer_signal.agent import EmployerSignalAgent
    from cdp.events import CDPClient

    pg_url = os.getenv("POSTGRES_URL", "")
    if not pg_url:
        console.print("[red]❌ POSTGRES_URL no configurada[/]"); raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("[cyan]Conectando...[/]")) as p:
        t = p.add_task("c")
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
        cdp  = CDPClient(postgres_url=pg_url); await cdp.connect()
        p.update(t, completed=True)

    agent = EmployerSignalAgent(cdp=cdp, pool=pool)

    # Paso 1: Simular señales
    with Progress(SpinnerColumn(), TextColumn(f"[cyan]Simulando {n} señales de empleadores...[/]")) as p:
        t = p.add_task("sim")
        count = await agent.simulate_employer_views(n=n)
        p.update(t, completed=True)
    console.print(f"[green]✅ {count} señales mock generadas en el CDP[/]\n")

    # Paso 2: Procesar señales (ventana amplia para encontrar las recién creadas)
    with Progress(SpinnerColumn(), TextColumn("[cyan]Procesando señales y generando notificaciones...[/]")) as p:
        t = p.add_task("proc")
        result = await agent.process_pending(window_minutes=60)
        p.update(t, completed=True)

    await agent.close(); await cdp.close(); await pool.close()

    if not result.results:
        console.print("[yellow]⚠ No se generaron notificaciones (posible deduplicación 24h activa)[/]")
        return

    table = Table(title="📧 Notificaciones enviadas", box=box.ROUNDED, show_lines=True)
    table.add_column("Candidato", style="white")
    table.add_column("Empresa", style="cyan")
    table.add_column("Vista previa", style="dim")
    table.add_column("Estado", justify="center", width=10)

    for r in result.results:
        state = "[green]✅[/]" if r.success else "[red]❌[/]"
        table.add_row(r.full_name, r.company_name,
                      r.notification_preview[:60] + ("…" if len(r.notification_preview) > 60 else ""),
                      state)
    console.print(table)
    console.print()

    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column(width=22); summary.add_column()
    summary.add_row("Señales procesadas:",   str(result.total_processed))
    summary.add_row("[green]✅ Enviadas:[/]", f"[green]{result.sent_ok}[/]")
    summary.add_row("[dim]⏭ Omitidas:[/]",   str(result.skipped))
    summary.add_row("Costo estimado:",       f"~${result.cost_usd:.4f} USD")
    console.print(Panel(summary, title="[bold]📊 Resumen[/]", border_style="blue"))
    console.print("[dim]Eventos [bold]employer.signal_notified[/] registrados en el CDP.[/]")


@app.command()
def main(
    n:      int  = typer.Option(5,     "--n",      help="Número de señales a simular"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Demo sin Claude"),
) -> None:
    """Demo del Employer Signal Agent — notifica candidatos cuando empresas ven su perfil."""
    asyncio.run(_run_demo(n=n, no_llm=no_llm))

if __name__ == "__main__":
    app()
