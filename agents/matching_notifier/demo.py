"""
Matching Notifier — Demo CLI

Uso:
    python -m agents.matching_notifier.demo            # vacantes últimas 24h (real)
    python -m agents.matching_notifier.demo --no-llm   # simulado sin Claude
    python -m agents.matching_notifier.demo --hours 72 # ampliar ventana
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

_MOCK_JOBS = [
    {"title": "Desarrollador Backend Python",  "company": "Rappi",        "city": "Bogotá"},
    {"title": "Analista de Datos Senior",       "company": "Bancolombia",  "city": "Medellín"},
    {"title": "Community Manager",              "company": "Crepes & Waffles", "city": "Bogotá"},
]

_MOCK_MATCHES = [
    {
        "job": _MOCK_JOBS[0],
        "candidates": [
            {"full_name": "Andrés García",  "title": "Dev Backend Junior",  "city": "Bogotá",   "score": 0.82},
            {"full_name": "Carlos Méndez",  "title": "Recién Graduado Sist.", "city": "Bogotá",  "score": 0.71},
        ],
        "notified": 2, "skipped": 0,
    },
    {
        "job": _MOCK_JOBS[1],
        "candidates": [
            {"full_name": "María Rodríguez","title": "Contadora Pública",   "city": "Medellín", "score": 0.78},
        ],
        "notified": 1, "skipped": 0,
    },
    {
        "job": _MOCK_JOBS[2],
        "candidates": [
            {"full_name": "Laura Jiménez",  "title": "Diseñadora Gráfica",  "city": "Bogotá",   "score": 0.61},
        ],
        "notified": 1, "skipped": 0,
    },
]


async def _run_demo(hours: int, no_llm: bool) -> None:
    console.rule("[bold blue]🔔 Matching Notifier — Elempleo AI Growth Engine")
    console.print()

    if no_llm:
        console.print(Panel(
            "[yellow]Modo offline — sin llamadas a Claude Haiku[/]\n"
            "Los matches son simulados para demo.",
            title="[bold yellow]⚡ Demo Offline[/]",
            border_style="yellow",
        ))
        console.print()
        _render_mock_results()
        return

    # ── Modo real ─────────────────────────────────────────────────────────────
    import asyncpg
    from agents.matching_notifier.agent import MatchingNotifierAgent
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

    agent     = MatchingNotifierAgent(cdp=cdp, pool=pool)
    new_jobs  = await agent._get_new_jobs(hours=hours)

    if not new_jobs:
        console.print(
            f"\n[green]✅ No hay vacantes nuevas en las últimas {hours}h sin procesar.[/]\n"
            "[dim]Tip: Los datos mock tienen published_at en el pasado. "
            "Usa [bold]--no-llm[/bold] para ver la demo con datos simulados.[/]"
        )
        await agent.close()
        await cdp.close()
        await pool.close()
        return

    console.print(f"[cyan]{len(new_jobs)} vacantes nuevas a procesar[/]\n")

    all_results = []
    with Progress(SpinnerColumn(), TextColumn("[cyan]Buscando matches y notificando...[/]")) as progress:
        task = progress.add_task("process", total=len(new_jobs))
        for job in new_jobs:
            result = await agent.process_job(job)
            all_results.append(result)
            progress.advance(task)

    await agent.close()
    await cdp.close()
    await pool.close()

    _render_real_results(all_results, hours)


def _render_mock_results() -> None:
    total_notified = 0
    for m in _MOCK_MATCHES:
        job = m["job"]
        table = Table(
            title=f"🔔 {job['title']} @ {job['company']} ({job['city']})",
            box=box.SIMPLE, show_lines=False,
        )
        table.add_column("Candidato", style="white")
        table.add_column("Cargo actual", style="dim")
        table.add_column("Ciudad", style="dim", width=12)
        table.add_column("Match %", justify="right", width=9)
        table.add_column("Estado", justify="center", width=9)

        for c in m["candidates"]:
            pct   = int(c["score"] * 100)
            color = "green" if pct >= 75 else "yellow" if pct >= 60 else "white"
            table.add_row(
                c["full_name"], c["title"], c["city"],
                f"[{color}]{pct}%[/{color}]", "[green]✅ Enviado[/]",
            )
            total_notified += 1

        console.print(table)

    console.print()
    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column(width=22); summary.add_column()
    summary.add_row("Vacantes procesadas:", str(len(_MOCK_MATCHES)))
    summary.add_row("[green]✅ Notificaciones:[/]", f"[green]{total_notified}[/]")
    summary.add_row("Costo estimado:", f"~${total_notified * 0.002:.4f} USD")
    console.print(Panel(summary, title="[bold]📊 Resumen[/]", border_style="blue"))
    console.print()
    console.print("[dim]Eventos [bold]match.notification_sent[/] registrados en el CDP.[/]")


def _render_real_results(results, hours: int) -> None:
    total_notified = sum(r.notified for r in results)
    total_skipped  = sum(r.skipped for r in results)

    for r in results:
        if not r.matched_users:
            continue
        table = Table(
            title=f"🔔 {r.job_title} @ {r.company} ({r.city})",
            box=box.SIMPLE, show_lines=False,
        )
        table.add_column("Candidato", style="white")
        table.add_column("Match %", justify="right", width=9)
        table.add_column("Estado", justify="center", width=12)

        for u in r.matched_users:
            pct   = int(u.match_score * 100)
            color = "green" if pct >= 75 else "yellow" if pct >= 60 else "white"
            state = "[green]✅ Enviado[/]" if u.notification_sent else "[dim]⏭ Omitido[/]"
            table.add_row(u.full_name, f"[{color}]{pct}%[/{color}]", state)

        console.print(table)

    console.print()
    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column(width=22); summary.add_column()
    summary.add_row("Vacantes procesadas:", str(len(results)))
    summary.add_row("[green]✅ Notificaciones:[/]", f"[green]{total_notified}[/]")
    summary.add_row("[dim]⏭ Omitidos:[/]",         str(total_skipped))
    summary.add_row("Costo estimado:", f"~${total_notified * 0.002:.4f} USD")
    console.print(Panel(summary, title="[bold]📊 Resumen[/]", border_style="blue"))
    console.print("[dim]Eventos [bold]match.notification_sent[/] registrados en el CDP.[/]")


@app.command()
def main(
    hours: int  = typer.Option(24,    "--hours",  "-h", help="Ventana de horas para vacantes nuevas"),
    no_llm: bool = typer.Option(False, "--no-llm",       help="Demo sin llamar a Claude"),
) -> None:
    """Demo del Matching Notifier — alerta candidatos cuando aparece una vacante de alto match."""
    asyncio.run(_run_demo(hours=hours, no_llm=no_llm))


if __name__ == "__main__":
    app()
