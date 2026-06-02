"""
Churn Predictor — Demo CLI
───────────────────────────
Muestra en terminal los usuarios en riesgo de churn,
clasificados por Claude Haiku con colores por nivel de riesgo.

Uso:
    python -m agents.churn_predictor.demo              # analiza inactivos >7 días (con LLM)
    python -m agents.churn_predictor.demo --days 14    # umbral de 14 días
    python -m agents.churn_predictor.demo --no-llm     # sin llamar a Claude (más rápido)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
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

BLUE  = "\033[34m"
RESET = "\033[0m"


def _mock_analyses(days: int) -> list[dict]:
    """Genera análisis mock cuando se usa --no-llm."""
    from datetime import timedelta
    import random

    mock_users = [
        {"full_name": "Ana García",      "days": days + 5,  "risk": "high",   "score": 0.85, "reason": "Sin actividad prolongada, perfil incompleto"},
        {"full_name": "Carlos Mora",     "days": days + 2,  "risk": "medium", "score": 0.55, "reason": "Inactividad moderada, pocas interacciones recientes"},
        {"full_name": "Luisa Pérez",     "days": days,      "risk": "low",    "score": 0.25, "reason": "Inactividad reciente pero historial de engagement bueno"},
        {"full_name": "Miguel Torres",   "days": days + 10, "risk": "high",   "score": 0.90, "reason": "Más de 3 semanas sin actividad y sin postulaciones"},
        {"full_name": "Sandra Vargas",   "days": days + 1,  "risk": "medium", "score": 0.48, "reason": "Perfil parcialmente completo, baja frecuencia de visitas"},
    ]

    results = []
    for u in mock_users:
        results.append({
            "full_name":          u["full_name"],
            "risk_level":         u["risk"],
            "risk_score":         u["score"],
            "risk_reason":        u["reason"],
            "key_signals":        [f"{u['days']} días inactivo", "Perfil incompleto"],
            "recommended_action": "send_reactivation" if u["risk"] == "high" else "monitor",
            "days_inactive":      u["days"],
        })
    return results


async def _run_demo(days: int, no_llm: bool) -> None:
    console.rule("[bold blue]🔍 Churn Predictor — Elempleo AI Growth Engine")
    console.print()

    if no_llm:
        # ── Modo offline ──────────────────────────────────────────────────────
        console.print(Panel(
            "[yellow]Modo offline — sin llamadas a Claude[/]\n"
            "Los análisis son simulados para demo.",
            title="[bold yellow]⚡ Demo Offline[/]",
            border_style="yellow",
        ))
        console.print()

        analyses = _mock_analyses(days)
        _render_results(analyses, days, cost=0.0)
        return

    # ── Modo real — conectar a Supabase y llamar a Claude ─────────────────────
    import asyncpg
    from agents.churn_predictor.agent import ChurnPredictorAgent
    from cdp.events import CDPClient

    pg_url = os.getenv("POSTGRES_URL", "")
    if not pg_url:
        console.print("[red]❌ POSTGRES_URL no configurada en .env[/]")
        raise typer.Exit(1)

    # Conectar
    with Progress(SpinnerColumn(), TextColumn("[cyan]Conectando a Supabase...[/]")) as p:
        t = p.add_task("connect")
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=3)
        cdp  = CDPClient(postgres_url=pg_url)
        await cdp.connect()
        p.update(t, completed=True)

    # Obtener usuarios inactivos
    users = await cdp.get_inactive_users(days_inactive=days)

    if not users:
        console.print(f"\n[green]✅ No hay usuarios inactivos hace más de {days} días.[/]")
        await cdp.close()
        await pool.close()
        return

    # Tabla de usuarios a analizar
    preview = Table(title=f"Usuarios inactivos (>{days} días)", box=box.SIMPLE)
    preview.add_column("Nombre", style="white")
    preview.add_column("Ciudad", style="dim")
    preview.add_column("Días inactivo", justify="right", style="yellow")
    preview.add_column("Perfil %", justify="right")

    for u in users[:10]:  # mostrar máx 10 en preview
        last_active = u.get("last_active_at")
        if last_active:
            if hasattr(last_active, "tzinfo") and last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            d_inactive = (datetime.now(timezone.utc) - last_active).days
        else:
            d_inactive = 0

        preview.add_row(
            u.get("full_name", "N/A"),
            u.get("city", "N/A"),
            str(d_inactive),
            f"{u.get('profile_completion', 0)}%",
        )

    if len(users) > 10:
        preview.add_row(f"... y {len(users) - 10} más", "", "", "")

    console.print(preview)
    console.print()

    # Analizar con Claude
    agent = ChurnPredictorAgent(cdp=cdp)
    raw_analyses = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]Analizando con Claude Haiku...[/]"),
    ) as progress:
        task = progress.add_task("analyze", total=len(users))

        for user in users:
            try:
                analysis = await agent.analyze_user(dict(user))
                raw_analyses.append({
                    "full_name":          analysis.full_name,
                    "risk_level":         analysis.risk_level.value,
                    "risk_score":         analysis.risk_score,
                    "risk_reason":        analysis.risk_reason,
                    "key_signals":        analysis.key_signals,
                    "recommended_action": analysis.recommended_action,
                    "days_inactive":      analysis.days_inactive,
                })
            except Exception as exc:
                console.print(f"[red]Error analizando {user.get('full_name')}: {exc}[/]")
            progress.advance(task)

    await agent.close()
    await cdp.close()
    await pool.close()

    _render_results(raw_analyses, days, cost=len(users) * 0.0004)


def _render_results(analyses: list[dict], days: int, cost: float) -> None:
    """Renderiza la tabla de resultados coloreada por riesgo."""
    if not analyses:
        console.print("[green]✅ Sin usuarios en riesgo.[/]")
        return

    # Tabla de resultados
    table = Table(
        title=f"Resultados del análisis de churn (inactivos >{days} días)",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Riesgo", justify="center", width=8)
    table.add_column("Nombre", style="white")
    table.add_column("Score", justify="right", width=7)
    table.add_column("Razón", style="dim")
    table.add_column("Acción", width=20)
    table.add_column("Días", justify="right", width=6)

    emoji_map  = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    color_map  = {"high": "red", "medium": "yellow", "low": "green"}
    action_map = {
        "send_reactivation": "[red]Reactivar ya[/]",
        "monitor":           "[yellow]Monitorear[/]",
        "no_action":         "[green]Sin acción[/]",
    }

    high = medium = low = 0
    for a in sorted(analyses, key=lambda x: x["risk_score"], reverse=True):
        risk    = a["risk_level"]
        emoji   = emoji_map.get(risk, "⚪")
        color   = color_map.get(risk, "white")
        action  = action_map.get(a["recommended_action"], a["recommended_action"])

        if risk == "high":   high   += 1
        elif risk == "medium": medium += 1
        else:                low    += 1

        table.add_row(
            f"[{color}]{emoji} {risk.upper()}[/{color}]",
            a["full_name"],
            f"[{color}]{a['risk_score']:.2f}[/{color}]",
            a["risk_reason"][:60] + ("..." if len(a["risk_reason"]) > 60 else ""),
            action,
            str(a["days_inactive"]),
        )

    console.print(table)
    console.print()

    # Resumen
    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column(width=20)
    summary.add_column()
    summary.add_row("Total analizados:", str(len(analyses)))
    summary.add_row("[red]🔴 Riesgo alto:[/]",   f"[red]{high}[/]")
    summary.add_row("[yellow]🟡 Riesgo medio:[/]", f"[yellow]{medium}[/]")
    summary.add_row("[green]🟢 Riesgo bajo:[/]",  f"[green]{low}[/]")
    if cost > 0:
        summary.add_row("Costo estimado:", f"~${cost:.4f} USD")

    console.print(Panel(summary, title="[bold]📊 Resumen[/]", border_style="blue"))
    console.print()
    console.print(
        "[dim]Los eventos [bold]churn.risk_detected[/] fueron registrados en el CDP. "
        "El Re-engagement Agent los procesará a continuación.[/]"
    )


@app.command()
def main(
    days: int = typer.Option(7, "--days", "-d", help="Días de inactividad para considerar en riesgo"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Demo sin llamar a Claude (resultados simulados)"),
) -> None:
    """Demo del Churn Predictor — detecta usuarios en riesgo de abandono."""
    asyncio.run(_run_demo(days=days, no_llm=no_llm))


if __name__ == "__main__":
    app()
