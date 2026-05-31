"""
Vector DB Setup — Qdrant
──────────────────────────
Crea la colección de vacantes en Qdrant y define los índices.
Ejecutar una sola vez al inicializar el entorno POC.

Uso:
    python -m vector_db.setup
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    VectorParams,
)

log = structlog.get_logger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────
QDRANT_URL        = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY")
COLLECTION_JOBS   = os.getenv("QDRANT_COLLECTION", "elempleo_jobs")
COLLECTION_USERS  = "elempleo_users"   # Para matching user↔job

# Dimensiones del modelo paraphrase-multilingual-MiniLM-L12-v2
VECTOR_SIZE = 384


def create_collections(client: QdrantClient) -> None:
    """Crea las colecciones en Qdrant si no existen."""

    # ── Colección: vacantes ─────────────────────────────────────────────
    existing = [c.name for c in client.get_collections().collections]

    if COLLECTION_JOBS not in existing:
        client.create_collection(
            collection_name=COLLECTION_JOBS,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )
        log.info("qdrant.collection_created", name=COLLECTION_JOBS)

        # Índices de payload para filtros rápidos
        client.create_payload_index(
            collection_name=COLLECTION_JOBS,
            field_name="city",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=COLLECTION_JOBS,
            field_name="category",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=COLLECTION_JOBS,
            field_name="modality",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=COLLECTION_JOBS,
            field_name="contract_type",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=COLLECTION_JOBS,
            field_name="is_active",
            field_schema=PayloadSchemaType.BOOL,
        )
        client.create_payload_index(
            collection_name=COLLECTION_JOBS,
            field_name="experience_years",
            field_schema=PayloadSchemaType.INTEGER,
        )
        log.info("qdrant.indexes_created", collection=COLLECTION_JOBS)
    else:
        log.info("qdrant.collection_exists", name=COLLECTION_JOBS)

    # ── Colección: perfiles de usuario ──────────────────────────────────
    if COLLECTION_USERS not in existing:
        client.create_collection(
            collection_name=COLLECTION_USERS,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )
        log.info("qdrant.collection_created", name=COLLECTION_USERS)
    else:
        log.info("qdrant.collection_exists", name=COLLECTION_USERS)


def verify_setup(client: QdrantClient) -> dict:
    """Verifica que las colecciones están bien configuradas."""
    result = {}
    for name in [COLLECTION_JOBS, COLLECTION_USERS]:
        try:
            info = client.get_collection(name)
            # vectors_count puede ser None en versiones nuevas de qdrant-client
            points = getattr(info, "points_count", None) or getattr(info, "vectors_count", 0)
            result[name] = {"status": "ok", "points_count": points}
        except Exception as e:
            result[name] = {"status": "error", "error": str(e)}
    return result


if __name__ == "__main__":
    import structlog
    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
    )
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    log.info("qdrant.connecting", url=QDRANT_URL)
    create_collections(client)
    status = verify_setup(client)
    log.info("qdrant.setup_complete", status=status)
