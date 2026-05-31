"""
Profile Optimizer — Demo CLI

Uso:
    python -m agents.profile_optimizer.demo            # real (Supabase + Qdrant + Sonnet)
    python -m agents.profile_optimizer.demo --no-llm   # simulado
"""
from __future__ import annotations
import asyncio, os
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
app = typer.Typer(add_completion=False)

_MOCK_REPORTS = [
    {
        "full_name": "Carlos Méndez", "current": 45, "projected": 78,
        "summary": "Con 3 cambios rápidos tu perfil estará en el top 30% de candidatos de Ingeniería de Sistemas.",
        "jobs": ["Desarrollador Backend Python", "Desarrollador Full Stack Jr", "Analista de Sistemas"],
        "suggestions": [
            {"priority": "high",   "field": "skills",   "suggested": "Agregar FastAPI, Docker y Git (presentes en 4/5 vacantes)", "effort": "5 min"},
            {"priority": "high",   "field": "title",    "suggested": "Cambiar a 'Desarrollador Backend Jr — Python & Java'", "effort": "5 min"},
            {"priority": "medium", "field": "experience","suggested": "Describir el proyecto de grado con tecnologías usadas", "effort": "30 min"},
        ],
    },
    {
        "full_name": "Sandra Vargas", "current": 55, "projected": 82,
        "summary": "Agregar tus certificaciones y completar el resumen profesional te daría visibilidad inmediata.",
        "jobs": ["Analista de Marketing Digital", "Community Manager", "Especialista en Redes Sociales"],
        "suggestions": [
            {"priority": "high",   "field": "skills",   "suggested": "Agregar Meta Ads, Google Analytics y Canva", "effort": "5 min"},
            {"priority": "medium", "field": "summary",  "suggested": "Redactar resumen profesional de 3 líneas con logros cuantificados", "effort": "30 min"},
            {"priority": "low",    "field": "photo",    "suggested": "Agregar foto profesional (perfil sin foto recibe 40% menos visitas)", "effort": "5 min"},
        ],
    },
]

_PRIORITY_COLORS = {"high": "red", "medium": "yellow", "low": "green"}
_PRIORITY_EMOJIS = {"high": "🔴", "medium": "🟡", "low": "🟢"}


async def _run_demo(no_llm: bool) -> None:
    console.rule("[bold blue]📋 Profile Optimizer — Elempleo AI Growth Engine")
    console.print()

    if no_llm:
        console.print(Panel(
            "[yellow]Modo offline — sin llamadas a Claude Sonnet[/]\n"
            "Los reportes son simulados para demo.",
            title="[bold yellow]⚡ Demo Offline[/]", border_style="yellow",
        ))
        console.print()
        for r in _MOCK_REPORTS:
            _render_report_mock(r)
        return

    # ── Modo real ─────────────────────────────────────────────────────────────
    import asyncpg
    from agents.profile_optimizer.agent import ProfileOptimizerAgent
    from cdp.events import CDPClient

    pg_url = os.getenv("POSTGRES_URL", "")
    if not pg_url:
        console.print("[red]❌ POSTGRES_URL no configurada[/]"); raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("[cyan]Conectando...[/]")) as p:
        t = p.add_task("connect")
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
        cdp  = CDPClient(postgres_url=pg_url); await cdp.connect()
        p.update(t, completed=True)

    agent = ProfileOptimizerAgent(cdp=cdp, pool=pool)
    users = await agent._get_users_to_optimize(max_completion=70)

    if not users:
        console.print(
            "\n[green]✅ No hay usuarios con perfil incompleto pendientes de optimizar.[/]\n"
            "[dim]Todos los usuarios tienen profile_completion >= 70% o recibieron sugerencias recientemente.[/]"
        )
        await agent.close(); await cdp.close(); await pool.close()
        return

    console.print(f"[cyan]{len(users)} usuarios con perfil incompleto encontrados[/]\n")

    reports = []
    with Progress(SpinnerColumn(), TextColumn("[cyan]Analizando perfiles con Claude Sonnet...[/]")) as progress:
        task = progress.add_task("analyze", total=len(users))
        for user in users:
            try:
                report = await agent.analyze_user(user)
                reports.append(report)
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/]")
            progress.advance(task)

    await agent.close(); await cdp.close(); await pool.close()

    for report in reports:
        _render_report_real(report)


def _render_report_mock(r: dict) -> None:
    completion_bar = "█" * (r["projected"] // 5) + "░" * (20 - r["projected"] // 5)

    header = Table(box=None, show_header=False)
    header.add_column(width=35); header.add_column()
    header.add_row(
        f"[bold white]{r['full_name']}[/]",
        f"[dim]Completitud: [red]{r['current']}%[/] → [green]{r['projected']}%[/]  +{r['projected']-r['current']}pp[/]"
    )
    console.print(header)
    console.print(f"[cyan]{completion_bar}[/]  {r['projected']}%")
    console.print(f"[dim italic]{r['summary']}[/]")
    console.print()

    # Vacantes de referencia
    console.print("[dim]Vacantes analizadas:[/] " + " · ".join(f"[dim]{j}[/]" for j in r["jobs"]))
    console.print()

    # Sugerencias
    table = Table(box=box.SIMPLE, show_lines=False, title="Sugerencias de mejora")
    table.add_column("P", width=3, justify="center")
    table.add_column("Campo", width=12)
    table.add_column("Sugerencia")
    table.add_column("Esfuerzo", width=10, justify="right", style="dim")

    for s in r["suggestions"]:
        p = s["priority"]; emoji = _PRIORITY_EMOJIS[p]; color = _PRIORITY_COLORS[p]
        table.add_row(
            f"[{color}]{emoji}[/{color}]",
            f"[bold]{s['field'].upper()}[/]",
            s["suggested"],
            s["effort"],
        )
    console.print(table)
    console.print()


def _render_report_real(report) -> None:
    improvement = report.projected_completion - report.current_completion
    console.print(f"\n[bold white]{report.full_name}[/]  "
                  f"[red]{report.current_completion}%[/] → [green]{report.projected_completion}%[/]  "
                  f"([green]+{improvement}pp[/])")
    console.print(f"[dim italic]{report.summary}[/]")
    if report.top_job_matches:
        console.print("[dim]Ref: " + " · ".join(report.top_job_matches[:3]) + "[/]")
    console.print()

    table = Table(box=box.SIMPLE, show_lines=False)
    table.add_column("P", width=3, justify="center")
    table.add_column("Campo", width=12)
    table.add_column("Sugerencia")
    table.add_column("Esfuerzo", width=10, justify="right", style="dim")

    for s in report.suggestions:
        color = _PRIORITY_COLORS.get(s.priority.value, "white")
        emoji = s.priority_emoji
        table.add_row(
            f"[{color}]{emoji}[/{color}]",
            f"[bold]{s.field.upper()}[/]",
            s.suggested[:70] + ("…" if len(s.suggested) > 70 else ""),
            s.effort,
        )
    console.print(table)


@app.command()
def main(no_llm: bool = typer.Option(False, "--no-llm", help="Demo sin Claude Sonnet")) -> None:
    """Demo del Profile Optimizer — genera sugerencias personalizadas de mejora de perfil."""
    asyncio.run(_run_demo(no_llm=no_llm))

if __name__ == "__main__":
    app()
