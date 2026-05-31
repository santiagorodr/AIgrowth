"""
Script de carga de datos mock al stack.
────────────────────────────────────────
1. Carga vacantes a PostgreSQL
2. Embebe vacantes en Qdrant (Vector DB)
3. Carga usuarios de prueba a PostgreSQL

Uso:
    python -m scripts.load_data
    python -m scripts.load_data --only-jobs
    python -m scripts.load_data --only-users
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

# Cargar .env ANTES de cualquier import del proyecto (embedder.py lee QDRANT_URL a nivel módulo)
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import asyncpg
import typer
from rich.console import Console
from rich.progress import track

from vector_db.embedder import JobEmbedder
from vector_db.setup import create_collections

console = Console()
app = typer.Typer()

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql://elempleo:elempleo_secret@localhost:5432/elempleo_poc",
)
DATA_DIR = ROOT / "data"


def to_uuid(raw_id: str) -> uuid.UUID:
    """Convierte un ID corto como 'job-001' en un UUID válido de forma determinista."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, raw_id)


async def load_jobs_to_postgres(pool: asyncpg.Pool, jobs: list[dict]) -> int:
    """Inserta vacantes en la tabla jobs."""
    count = 0
    async with pool.acquire() as conn:
        for job in track(jobs, description="Cargando vacantes a PostgreSQL..."):
            await conn.execute(
                """
                INSERT INTO jobs (
                    id, title, company, city, description, requirements, benefits,
                    salary_min, salary_max, contract_type, modality,
                    experience_years, education_level, category, skills_required, is_active
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    is_active = EXCLUDED.is_active
                """,
                to_uuid(job["id"]),
                job["title"],
                job["company"],
                job.get("city"),
                job.get("description"),
                job.get("requirements"),
                job.get("benefits"),
                job.get("salary_min"),
                job.get("salary_max"),
                job.get("contract_type"),
                job.get("modality"),
                job.get("experience_years", 0),
                job.get("education_level"),
                job.get("category"),
                job.get("skills_required", []),
                job.get("is_active", True),
            )
            count += 1
    return count


async def load_users_to_postgres(pool: asyncpg.Pool, users: list[dict]) -> int:
    """Inserta usuarios de prueba en la tabla users."""
    count = 0
    async with pool.acquire() as conn:
        for user in track(users, description="Cargando usuarios a PostgreSQL..."):
            await conn.execute(
                """
                INSERT INTO users (
                    id, email, phone, full_name, source,
                    current_title, current_company, experience_years,
                    education_level, city, desired_salary, skills,
                    profile_completion, is_active
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                ON CONFLICT (email) DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    profile_completion = EXCLUDED.profile_completion
                """,
                to_uuid(user["id"]),
                user["email"],
                user.get("phone"),
                user["full_name"],
                user.get("source", "organic"),
                user.get("current_title"),
                user.get("current_company"),
                user.get("experience_years", 0),
                user.get("education_level"),
                user.get("city"),
                user.get("desired_salary"),
                user.get("skills", []),
                user.get("profile_completion", 0),
                user.get("is_active", True),
            )
            count += 1
    return count


@app.command()
def main(
    only_jobs: bool = typer.Option(False, "--only-jobs"),
    only_users: bool = typer.Option(False, "--only-users"),
):
    asyncio.run(_run(only_jobs=only_jobs, only_users=only_users))


async def _run(only_jobs: bool = False, only_users: bool = False):
    console.rule("[bold blue]Elempleo — Carga de datos mock")

    # Leer archivos
    jobs = json.loads((DATA_DIR / "mock_jobs.json").read_text())
    users = json.loads((DATA_DIR / "mock_users.json").read_text())
    embedder = None  # se inicializa lazy cuando se necesita

    console.print(f"✅ Archivos leídos: [cyan]{len(jobs)} vacantes[/], [cyan]{len(users)} usuarios[/]")

    # Conectar a PostgreSQL
    pool = await asyncpg.create_pool(POSTGRES_URL)
    console.print("✅ PostgreSQL conectado")

    if not only_users:
        # Cargar vacantes a PostgreSQL
        n_jobs = await load_jobs_to_postgres(pool, jobs)
        console.print(f"✅ [green]{n_jobs} vacantes[/] cargadas en PostgreSQL")

        # Configurar Qdrant y embeber
        console.print("\n[bold]Configurando Qdrant...[/]")
        from qdrant_client import QdrantClient
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        create_collections(client)
        console.print("✅ Colecciones Qdrant listas")

        console.print("\n[bold]Embebiendo vacantes (esto toma ~1 min la primera vez)...[/]")
        embedder = JobEmbedder()
        n_embedded = embedder.index_jobs(jobs)
        console.print(f"✅ [green]{n_embedded} vacantes[/] indexadas en Qdrant")

    if not only_jobs:
        # Cargar usuarios a PostgreSQL
        n_users = await load_users_to_postgres(pool, users)
        console.print(f"✅ [green]{n_users} usuarios[/] cargados en PostgreSQL")

        # Indexar usuarios en Qdrant (búsqueda inversa para Matching Notifier)
        console.print("\n[bold]Indexando perfiles de usuarios en Qdrant...[/]")
        if embedder is None:
            embedder = JobEmbedder()
        for u in users:
            try:
                embedder.index_user(u)
            except Exception as exc:
                console.print(f"  [yellow]⚠ No se pudo indexar usuario {u.get('id')}: {exc}[/]")
        console.print(f"✅ [green]{len(users)} usuarios[/] indexados en Qdrant (elempleo_users)")

    await pool.close()

    console.rule("[bold green]✅ Carga completada")
    console.print("\n[bold]Próximo paso:[/] ejecuta [cyan]make test[/] para verificar el stack.")


if __name__ == "__main__":
    app()
