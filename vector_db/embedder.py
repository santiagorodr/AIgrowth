"""
Embedder de Vacantes y Perfiles
─────────────────────────────────
Convierte vacantes y perfiles de usuario en vectores
usando sentence-transformers (gratis, sin API key, soporta español).

Modelo: paraphrase-multilingual-MiniLM-L12-v2
- Tamaño: ~470MB (descarga automática la primera vez)
- Velocidad: ~2000 frases/segundo en CPU
- Calidad: buena para español y textos cortos-medianos
- Sin costos de API

Uso:
    embedder = JobEmbedder()
    await embedder.index_jobs(jobs_list)

    results = await embedder.search(
        query="desarrollador backend python bogotá",
        filters={"city": "Bogotá", "is_active": True},
        top_k=10
    )
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct
from sentence_transformers import SentenceTransformer

log = structlog.get_logger(__name__)

COLLECTION_JOBS  = os.getenv("QDRANT_COLLECTION", "elempleo_jobs")
COLLECTION_USERS = "elempleo_users"
MODEL_NAME       = "paraphrase-multilingual-MiniLM-L12-v2"


class JobEmbedder:
    """
    Indexa y busca vacantes en Qdrant usando embeddings semánticos.
    El mismo modelo embebe tanto vacantes como queries de búsqueda,
    lo que permite matching semántico real (no solo keywords).
    """

    def __init__(self):
        # Leer credenciales en __init__ (no a nivel módulo) para que
        # load_dotenv() ya haya corrido cuando se instancia el embedder.
        qdrant_url     = os.getenv("QDRANT_URL", "http://localhost:6333")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        log.info("embedder.loading_model", model=MODEL_NAME)
        self._model  = SentenceTransformer(MODEL_NAME)
        self._qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        log.info("embedder.ready")

    def _job_to_text(self, job: dict) -> str:
        """
        Concatena los campos más relevantes de una vacante para embedding.
        El texto resultante captura el 'significado' de la vacante.
        """
        parts = [
            job.get("title", ""),
            job.get("company", ""),
            job.get("city", ""),
            job.get("category", ""),
            job.get("description", "")[:500],  # primeros 500 chars
            job.get("requirements", "")[:300],
            " ".join(job.get("skills_required", [])),
            job.get("modality", ""),
            job.get("contract_type", ""),
        ]
        return " | ".join(p for p in parts if p)

    def _user_to_text(self, user: dict) -> str:
        """Convierte perfil de usuario en texto embebible."""
        parts = [
            user.get("current_title", ""),
            user.get("city", ""),
            " ".join(user.get("skills", [])),
            user.get("education_level", ""),
            f"{user.get('experience_years', 0)} años de experiencia",
        ]
        return " | ".join(p for p in parts if p)

    # ── Indexar vacantes ──────────────────────────────────────────────────
    def index_jobs(self, jobs: list[dict]) -> int:
        """
        Embebe e indexa una lista de vacantes en Qdrant.
        Retorna el número de vacantes indexadas.
        """
        if not jobs:
            return 0

        texts = [self._job_to_text(j) for j in jobs]
        log.info("embedder.encoding_jobs", count=len(texts))

        vectors = self._model.encode(texts, show_progress_bar=True, batch_size=32)

        points = []
        for job, vector in zip(jobs, vectors):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, job.get("id", str(uuid.uuid4()))))
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector.tolist(),
                    payload={
                        "job_id": job.get("id"),
                        "title": job.get("title"),
                        "company": job.get("company"),
                        "city": job.get("city"),
                        "category": job.get("category"),
                        "modality": job.get("modality"),
                        "contract_type": job.get("contract_type"),
                        "salary_min": job.get("salary_min"),
                        "salary_max": job.get("salary_max"),
                        "experience_years": job.get("experience_years", 0),
                        "skills_required": job.get("skills_required", []),
                        "is_active": job.get("is_active", True),
                    },
                )
            )

        # Upsert en batches de 100
        batch_size = 100
        for i in range(0, len(points), batch_size):
            self._qdrant.upsert(
                collection_name=COLLECTION_JOBS,
                points=points[i : i + batch_size],
            )

        log.info("embedder.jobs_indexed", count=len(points))
        return len(points)

    # ── Indexar perfil de usuario ─────────────────────────────────────────
    def index_user(self, user: dict) -> str:
        """Embebe e indexa el perfil de un usuario. Retorna el point_id."""
        text = self._user_to_text(user)
        vector = self._model.encode([text])[0]
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, user["id"]))

        self._qdrant.upsert(
            collection_name=COLLECTION_USERS,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector.tolist(),
                    payload={
                        "user_id": user["id"],
                        "city": user.get("city"),
                        "skills": user.get("skills", []),
                        "experience_years": user.get("experience_years", 0),
                    },
                )
            ],
        )
        return point_id

    # ── Buscar vacantes ───────────────────────────────────────────────────
    def search_jobs(
        self,
        query: str,
        top_k: int = 10,
        city: str | None = None,
        category: str | None = None,
        modality: str | None = None,
        only_active: bool = True,
    ) -> list[dict]:
        """
        Búsqueda semántica de vacantes.
        Combina similitud vectorial + filtros de payload.

        Args:
            query: Texto libre del usuario, ej "desarrollador python con experiencia en Django"
            top_k: Número de resultados
            city: Filtrar por ciudad
            category: Filtrar por categoría
            modality: 'presencial' | 'remoto' | 'hibrido'
            only_active: Solo vacantes activas

        Returns:
            Lista de vacantes ordenadas por relevancia
        """
        query_vector = self._model.encode([query])[0].tolist()

        # Construir filtros
        conditions = []
        if only_active:
            conditions.append(FieldCondition(key="is_active", match=MatchValue(value=True)))
        if city:
            conditions.append(FieldCondition(key="city", match=MatchValue(value=city)))
        if category:
            conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
        if modality:
            conditions.append(FieldCondition(key="modality", match=MatchValue(value=modality)))

        search_filter = Filter(must=conditions) if conditions else None

        response = self._qdrant.query_points(
            collection_name=COLLECTION_JOBS,
            query=query_vector,
            query_filter=search_filter,
            limit=top_k,
            with_payload=True,
            score_threshold=0.3,
        )

        return [
            {
                **r.payload,
                "relevance_score": round(r.score, 4),
            }
            for r in response.points
        ]

    # ── Búsqueda inversa: vacante → candidatos ────────────────────────────
    def search_users(
        self,
        query: str,
        top_k: int = 30,
        city: str | None = None,
        score_threshold: float = 0.45,
    ) -> list[dict]:
        """
        Búsqueda inversa: dado el texto de una vacante, encuentra los candidatos
        cuyo perfil es más similar. Usado por el Matching Notifier.

        Args:
            query:           Texto de la vacante (título + habilidades + descripción)
            top_k:           Número máximo de candidatos a retornar
            city:            Filtrar por ciudad del candidato (opcional)
            score_threshold: Score mínimo de similitud coseno (default 0.45)

        Returns:
            Lista de perfiles de candidatos ordenados por relevancia
        """
        query_vector = self._model.encode([query])[0].tolist()

        conditions = []
        if city:
            conditions.append(FieldCondition(key="city", match=MatchValue(value=city)))

        search_filter = Filter(must=conditions) if conditions else None

        response = self._qdrant.query_points(
            collection_name=COLLECTION_USERS,
            query=query_vector,
            query_filter=search_filter,
            limit=top_k,
            with_payload=True,
            score_threshold=score_threshold,
        )

        return [
            {**r.payload, "relevance_score": round(r.score, 4)}
            for r in response.points
        ]

    # ── Recomendar vacantes para un usuario ───────────────────────────────
    def recommend_for_user(
        self,
        user: dict,
        top_k: int = 10,
        city: str | None = None,
    ) -> list[dict]:
        """
        Recomienda vacantes para un usuario basándose en su perfil.
        Usa el perfil completo del usuario como query.
        """
        query = self._user_to_text(user)
        return self.search_jobs(
            query=query,
            top_k=top_k,
            city=city or user.get("city"),
        )


# ── Singleton ──────────────────────────────────────────────────────────────
_embedder: JobEmbedder | None = None


def get_embedder() -> JobEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = JobEmbedder()
    return _embedder
