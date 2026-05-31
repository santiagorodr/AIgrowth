"""
Health Check del Stack Completo
──────────────────────────────────
Verifica que todos los servicios de la infraestructura
estén corriendo y respondiendo correctamente.

Uso:
    python -m scripts.health_check
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")  # carga explícita desde la raíz del proyecto

from rich.console import Console
from rich.table import Table

console = Console()

POSTGRES_URL    = os.getenv("POSTGRES_URL", "postgresql://elempleo:elempleo_secret@localhost:5432/elempleo_poc")
QDRANT_URL      = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY", "")
GATEWAY_URL     = os.getenv("GATEWAY_URL", "http://localhost:8000")


async def check_postgres() -> dict:
    try:
        pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=2, timeout=10)
        async with pool.acquire() as conn:
            row   = await conn.fetchrow("SELECT COUNT(*) as jobs FROM jobs")
            users = await conn.fetchrow("SELECT COUNT(*) as users FROM users")
        await pool.close()
        return {
            "status": "✅ OK",
            "detail": f"{row['jobs']} vacantes, {users['users']} usuarios",
        }
    except Exception as e:
        return {"status": "❌ ERROR", "detail": str(e)}


async def check_qdrant() -> dict:
    try:
        headers = {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else {}
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{QDRANT_URL}/collections", headers=headers)
            data = r.json()
            collections = [c["name"] for c in data.get("result", {}).get("collections", [])]
            return {"status": "✅ OK", "detail": f"Colecciones: {', '.join(collections) or 'ninguna'}"}
    except Exception as e:
        return {"status": "❌ ERROR", "detail": str(e)}


async def check_gateway() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{GATEWAY_URL}/health")
            data = r.json()
            return {
                "status": "✅ OK" if data.get("status") == "ok" else "⚠️  DEGRADED",
                "detail": f"Anthropic API: {data.get('anthropic_api')}, "
                          f"Llamadas hoy: {data.get('total_calls_today', 0)}, "
                          f"Costo hoy: ${data.get('total_cost_today_usd', 0):.4f}",
            }
    except Exception as e:
        return {"status": "❌ ERROR", "detail": str(e)}


async def check_embeddings() -> dict:
    try:
        # Corre en thread para no bloquear el event loop durante carga del modelo ML
        def _run():
            from vector_db.embedder import JobEmbedder
            embedder = JobEmbedder()
            return embedder.search_jobs("desarrollador python bogotá", top_k=3)

        results = await asyncio.to_thread(_run)
        if results:
            top = results[0]
            return {
                "status": "✅ OK",
                "detail": f"{len(results)} resultados · Top: '{top['title']}' ({top['relevance_score']:.2f})",
            }
        return {"status": "⚠️  VACÍO", "detail": "Qdrant sin datos. Ejecuta: make load-data"}
    except Exception as e:
        return {"status": "❌ ERROR", "detail": repr(e)}


async def run_all():
    console.rule("[bold blue]🏥 Health Check — Elempleo AI Growth Engine")
    console.print()

    checks = {
        "PostgreSQL · Supabase":   check_postgres(),
        "Qdrant · Vector DB":      check_qdrant(),
        "LLM Gateway (FastAPI)":   check_gateway(),
        "Embeddings (búsqueda)":   check_embeddings(),
    }

    results = await asyncio.gather(*checks.values(), return_exceptions=True)

    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("Servicio", style="white", width=25)
    table.add_column("Estado", width=14)
    table.add_column("Detalle", style="dim")

    all_ok = True
    for (name, _), result in zip(checks.items(), results):
        if isinstance(result, Exception):
            result = {"status": "❌ ERROR", "detail": repr(result)}
        if not isinstance(result, dict):
            result = {"status": "❌ ERROR", "detail": repr(result)}
        if "ERROR" in result["status"]:
            all_ok = False
        table.add_row(name, result["status"], result.get("detail", ""))

    console.print(table)
    console.print()

    if all_ok:
        console.print("[bold green]✅ Stack 100% operativo. ¡Listo para construir agentes![/]")
    else:
        console.print("[bold yellow]⚠️  Algunos servicios tienen problemas. Revisa tu .env (credenciales Supabase / Qdrant Cloud).[/]")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
