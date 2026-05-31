"""
Demo CLI del Job Match Agent
──────────────────────────────
Prueba el agente en terminal con perfiles del mock_users.json.
Muestra el pipeline completo: búsqueda semántica → reranking LLM → resultado.

Uso:
    # Demo interactiva (elige un usuario)
    python -m agents.job_match.demo

    # Demo con un usuario específico (por índice 0-19)
    python -m agents.job_match.demo --user 2

    # Demo con todos los usuarios (modo batch)
    python -m agents.job_match.demo --all

    # Demo con perfil custom
    python -m agents.job_match.demo --custom
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# Cargar .env ANTES de importar embedder (lee QDRANT_URL a nivel módulo)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import typer
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from agents.job_match.agent import JobMatchAgent
from agents.job_match.models import JobMatchRequest, JobMatchResult, UserProfile

console = Console()
app = typer.Typer(add_completion=False)

MOCK_USERS_PATH = ROOT / "data" / "mock_users.json"

# Colores por score
def score_color(score: float) -> str:
    if score >= 0.85:
        return "bold green"
    if score >= 0.70:
        return "green"
    if score >= 0.55:
        return "yellow"
    return "dim"


def score_bar(score: float, width: int = 12) -> str:
    filled = int(score * width)
    return "█" * filled + "░" * (width - filled)


def format_salary(min_sal: int | None, max_sal: int | None) -> str:
    if min_sal and max_sal:
        return f"${min_sal/1_000_000:.1f}M – ${max_sal/1_000_000:.1f}M"
    if min_sal:
        return f"desde ${min_sal/1_000_000:.1f}M"
    return "No especificado"


def print_user_profile(user: UserProfile) -> None:
    skills_str = " · ".join(user.skills[:6]) if user.skills else "No especificadas"
    salary_str = f"${user.desired_salary/1_000_000:.1f}M COP" if user.desired_salary else "No especificado"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", width=20)
    grid.add_column(style="white")
    grid.add_row("Cargo actual",     user.current_title or "—")
    grid.add_row("Empresa",          user.current_company or "En búsqueda")
    grid.add_row("Ciudad",           user.city or "—")
    grid.add_row("Experiencia",      f"{user.experience_years} años")
    grid.add_row("Educación",        user.education_level or "—")
    grid.add_row("Skills",           skills_str)
    grid.add_row("Salario deseado",  salary_str)

    console.print(Panel(
        grid,
        title=f"[bold white]👤 {user.full_name}[/bold white]",
        border_style="blue",
        padding=(1, 2),
    ))


def print_result(result: JobMatchResult) -> None:
    # Resumen del agente
    console.print()
    console.print(Panel(
        f"[italic]{result.agent_summary}[/italic]",
        title="[bold yellow]💡 Análisis del Agente[/bold yellow]",
        border_style="yellow",
        padding=(0, 2),
    ))

    console.print()
    console.print(f"[bold]Se evaluaron [cyan]{result.total_candidates_evaluated}[/cyan] vacantes · "
                  f"Mostrando las [cyan]{len(result.jobs)}[/cyan] más relevantes[/bold]")
    console.print()

    for i, job in enumerate(result.jobs, 1):
        # Header de la vacante
        score_txt = Text()
        score_txt.append(f"  {score_bar(job.match_score)}  ", style=score_color(job.match_score))
        score_txt.append(f"{job.match_score:.0%} match", style=f"bold {score_color(job.match_score)}")

        modality_icon = {"remoto": "🏠", "hibrido": "🔄", "presencial": "🏢"}.get(
            job.modality.lower() if job.modality else "", "📍"
        )

        header = Table.grid(padding=(0, 1))
        header.add_column(width=3)
        header.add_column(style="bold white", ratio=1)
        header.add_column()
        header.add_row(
            f"[dim]{i}.[/dim]",
            f"{job.title}  [dim]@[/dim] [cyan]{job.company}[/cyan]",
            score_txt,
        )

        # Detalles
        details = Table.grid(padding=(0, 2))
        details.add_column(style="dim", width=2)
        details.add_column()

        details.add_row("📍", f"{job.city}  {modality_icon} {job.modality or '—'}  "
                              f"📄 {job.contract_type or '—'}  "
                              f"💰 {format_salary(job.salary_min, job.salary_max)}")

        # Highlights (badges)
        if job.highlights:
            badges = "  ".join(f"[bold green]✓[/bold green] {h}" for h in job.highlights)
            details.add_row("🏷", badges)

        # Razón del match
        details.add_row("💬", f"[italic dim]{job.match_reason}[/italic dim]")

        console.print(Panel(
            Columns([header, details], equal=False, expand=True),
            border_style=score_color(job.match_score),
            padding=(0, 1),
        ))

    console.print()


async def run_for_user(user_dict: dict, top_k: int = 8) -> None:
    agent = JobMatchAgent()
    try:
        user = UserProfile(**{
            k: user_dict[k]
            for k in UserProfile.model_fields
            if k in user_dict
        })

        print_user_profile(user)
        console.print()

        with console.status("[bold blue]Buscando vacantes semánticamente...[/bold blue]"):
            t0 = time.time()
            result = await agent.run(JobMatchRequest(user=user, top_k=top_k))
            elapsed = time.time() - t0

        console.print(f"[dim]Completado en {elapsed:.1f}s[/dim]")
        print_result(result)

    finally:
        await agent.close()


@app.command()
def main(
    user: int = typer.Option(None, "--user", "-u", help="Índice del usuario (0-19)"),
    all_users: bool = typer.Option(False, "--all", help="Correr con todos los usuarios"),
    custom: bool = typer.Option(False, "--custom", help="Ingresar perfil manualmente"),
    top_k: int = typer.Option(8, "--top-k", "-k", help="Número de recomendaciones"),
):
    asyncio.run(_main(user_idx=user, all_users=all_users, custom=custom, top_k=top_k))


async def _main(
    user_idx: int | None,
    all_users: bool,
    custom: bool,
    top_k: int,
) -> None:
    console.print(Rule("[bold blue]🎯 Job Match Personalization Agent — Demo[/bold blue]"))
    console.print()

    users = json.loads(MOCK_USERS_PATH.read_text())

    # ── Modo custom ──────────────────────────────────────────────────────────
    if custom:
        console.print("[bold]Ingresa los datos del perfil:[/bold]\n")
        user_dict = {
            "id": "custom-001",
            "full_name":        Prompt.ask("Nombre completo"),
            "current_title":    Prompt.ask("Cargo actual"),
            "city":             Prompt.ask("Ciudad", default="Bogotá"),
            "experience_years": IntPrompt.ask("Años de experiencia", default=2),
            "education_level":  Prompt.ask("Nivel educativo", default="profesional"),
            "skills":           Prompt.ask("Skills (separados por coma)").split(","),
            "desired_salary":   IntPrompt.ask("Salario deseado en COP", default=5000000),
        }
        user_dict["skills"] = [s.strip() for s in user_dict["skills"] if s.strip()]
        console.print()
        await run_for_user(user_dict, top_k)
        return

    # ── Modo all ────────────────────────────────────────────────────────────
    if all_users:
        for i, u in enumerate(users):
            console.print(Rule(f"[dim]Usuario {i+1}/{len(users)}[/dim]"))
            await run_for_user(u, top_k)
        return

    # ── Modo interactivo: elegir usuario ─────────────────────────────────────
    if user_idx is None:
        console.print("[bold]Usuarios disponibles:[/bold]\n")
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("#", width=4, style="dim")
        table.add_column("Nombre", width=30)
        table.add_column("Cargo", width=35)
        table.add_column("Ciudad", width=12)
        table.add_column("Skills")

        for i, u in enumerate(users):
            skills_preview = ", ".join(u.get("skills", [])[:3])
            table.add_row(
                str(i),
                u["full_name"],
                u.get("current_title", "—"),
                u.get("city", "—"),
                skills_preview,
            )

        console.print(table)
        console.print()
        user_idx = IntPrompt.ask(
            "Elige el número de usuario",
            default=0,
            choices=[str(i) for i in range(len(users))],
        )

    if user_idx < 0 or user_idx >= len(users):
        console.print(f"[red]Índice {user_idx} fuera de rango (0-{len(users)-1})[/red]")
        raise SystemExit(1)

    console.print()
    await run_for_user(users[user_idx], top_k)


if __name__ == "__main__":
    app()
