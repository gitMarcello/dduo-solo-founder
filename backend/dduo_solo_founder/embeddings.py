from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from openai import OpenAI
from qdrant_client import QdrantClient, models

from dduo_solo_founder.config import Settings, get_settings


@dataclass(slots=True)
class EmbeddingUsage:
    provider: str
    model: str
    input_tokens: int | None
    reported_total_tokens: int | None
    measurement_source: str


@dataclass(slots=True)
class EmbeddingCall:
    vectors: list[list[float]]
    usage: EmbeddingUsage
    provider_duration_ms: int
    request_count: int = 1


@dataclass(slots=True)
class VectorSearchResult:
    items: list[dict]
    embedding: EmbeddingCall
    vector_store_duration_ms: int


@dataclass(slots=True)
class VectorUpsertResult:
    embedding: EmbeddingCall
    vector_store_duration_ms: int


@dataclass(slots=True)
class VectorPayloadUpdateResult:
    vector_store_duration_ms: int


class VectorOperationError(RuntimeError):
    """Preserve a successful paid embedding when Qdrant fails afterwards."""

    def __init__(
        self,
        message: str,
        *,
        embedding: EmbeddingCall | None = None,
        vector_store_duration_ms: int | None = None,
    ):
        super().__init__(message)
        self.embedding = embedding
        self.vector_store_duration_ms = vector_store_duration_ms


class EmbeddingProviderError(RuntimeError):
    """One logical provider invocation failed before returning usage."""

    def __init__(self, message: str, *, provider: str, model: str, duration_ms: int):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.provider_duration_ms = duration_ms
        self.provider_request_count = 1


def provider_request_count(
    embedding: EmbeddingCall | None, error: Exception | None = None
) -> int:
    """Count logical SDK/model calls; SDK-internal HTTP retries are not observable."""
    if embedding is not None:
        return int(embedding.request_count)
    return int(getattr(error, "provider_request_count", 0) or 0)


def _usage_value(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    return int(value) if isinstance(value, int | float) else None


class EmbeddingService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.qdrant = QdrantClient(url=self.settings.qdrant_url, check_compatibility=False)
        self._openai = None

    @property
    def dimension(self) -> int:
        if self.settings.embedding_provider == "openai":
            return 3072
        if "MiniLM" in self.settings.embedding_model or "small" in self.settings.embedding_model:
            return 384
        if "mpnet" in self.settings.embedding_model:
            return 768
        return 1024

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        return self.embed_observed(texts, query=query).vectors

    def embed_observed(self, texts: list[str], *, query: bool = False) -> EmbeddingCall:
        started = perf_counter()
        if self.settings.embedding_provider == "openai":
            self._openai = self._openai or OpenAI(api_key=self.settings.openai_api_key)
            try:
                response = self._openai.embeddings.create(
                    model=self.settings.embedding_model, input=texts
                )
            except Exception as exc:
                raise EmbeddingProviderError(
                    str(exc),
                    provider="openai",
                    model=self.settings.embedding_model,
                    duration_ms=round((perf_counter() - started) * 1000),
                ) from exc
            usage = getattr(response, "usage", None)
            input_tokens = _usage_value(usage, "prompt_tokens")
            total_tokens = _usage_value(usage, "total_tokens")
            return EmbeddingCall(
                vectors=[item.embedding for item in response.data],
                usage=EmbeddingUsage(
                    provider="openai",
                    model=self.settings.embedding_model,
                    input_tokens=input_tokens,
                    reported_total_tokens=total_tokens,
                    measurement_source=(
                        "provider_reported" if input_tokens is not None else "unavailable"
                    ),
                ),
                provider_duration_ms=round((perf_counter() - started) * 1000),
            )

        model = local_model(self.settings.embedding_model)
        prefix = (
            "query: " if query and "e5" in self.settings.embedding_model.lower() else "passage: "
        )
        values = [
            prefix + value if "e5" in self.settings.embedding_model.lower() else value
            for value in texts
        ]
        try:
            vectors = [vector.tolist() for vector in model.embed(values)]
        except Exception as exc:
            raise EmbeddingProviderError(
                str(exc),
                provider=self.settings.embedding_provider,
                model=self.settings.embedding_model,
                duration_ms=round((perf_counter() - started) * 1000),
            ) from exc
        return EmbeddingCall(
            vectors=vectors,
            usage=EmbeddingUsage(
                provider=self.settings.embedding_provider,
                model=self.settings.embedding_model,
                input_tokens=None,
                reported_total_tokens=None,
                measurement_source="unavailable",
            ),
            provider_duration_ms=round((perf_counter() - started) * 1000),
        )

    def collection(self, project_id: str) -> str:
        model_slug = self.settings.embedding_model.replace("/", "_").replace("-", "_")
        provider = self.settings.embedding_provider
        version = self.settings.embedding_index_version
        return (
            f"{self.settings.collection_prefix}_{project_id.replace('-', '_')}_"
            f"{provider}_{model_slug}_{version}"
        )

    def task_collection(self, project_id: str) -> str:
        """Return the independent, rebuildable task projection collection."""
        model_slug = self.settings.embedding_model.replace("/", "_").replace("-", "_")
        provider = self.settings.embedding_provider
        version = self.settings.task_embedding_index_version
        return (
            f"{self.settings.collection_prefix}_tasks_{project_id.replace('-', '_')}_"
            f"{provider}_{model_slug}_{version}"
        )

    def _ensure_named_collection(self, name: str) -> str:
        existing = {item.name for item in self.qdrant.get_collections().collections}
        if name not in existing:
            try:
                self.qdrant.create_collection(
                    name,
                    vectors_config=models.VectorParams(
                        size=self.dimension, distance=models.Distance.COSINE
                    ),
                )
            except Exception:
                # API retrieval and an outbox worker may initialize the same
                # projection concurrently. Accept only a confirmed winner.
                current = {item.name for item in self.qdrant.get_collections().collections}
                if name not in current:
                    raise
        return name

    def ensure_collection(self, project_id: str) -> str:
        return self._ensure_named_collection(self.collection(project_id))

    def ensure_task_collection(self, project_id: str) -> str:
        return self._ensure_named_collection(self.task_collection(project_id))

    def task_collection_exists(self, project_id: str) -> bool:
        name = self.task_collection(project_id)
        exists = getattr(self.qdrant, "collection_exists", None)
        if callable(exists):
            return bool(exists(name))
        return name in {item.name for item in self.qdrant.get_collections().collections}

    @staticmethod
    def task_marker_id(project_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"dduo-task-index-marker:{project_id}"))

    def task_marker_epoch(self, project_id: str) -> str | None:
        """Read the collection identity without creating a missing collection."""
        if not self.task_collection_exists(project_id):
            return None
        points = self.qdrant.retrieve(
            collection_name=self.task_collection(project_id),
            ids=[self.task_marker_id(project_id)],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        payload = points[0].payload or {}
        if payload.get("record_type") != "task_index_marker":
            return None
        epoch = payload.get("epoch")
        return str(epoch) if epoch else None

    def task_projection_count(self, project_id: str) -> int | None:
        """Return exact derived cardinality without creating a missing collection."""
        if not self.task_collection_exists(project_id):
            return None
        result = self.qdrant.count(
            collection_name=self.task_collection(project_id),
            exact=True,
        )
        # A reconciled task collection contains exactly one marker point.
        return max(int(result.count) - 1, 0)

    def task_integrity_snapshot(self, project_id: str) -> tuple[str, int] | None:
        """Read collection identity and exact task count with one existence check."""
        if not self.task_collection_exists(project_id):
            return None
        collection = self.task_collection(project_id)
        points = self.qdrant.retrieve(
            collection_name=collection,
            ids=[self.task_marker_id(project_id)],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        payload = points[0].payload or {}
        epoch = payload.get("epoch")
        if payload.get("record_type") != "task_index_marker" or not epoch:
            return None
        result = self.qdrant.count(collection_name=collection, exact=True)
        return str(epoch), max(int(result.count) - 1, 0)

    def write_task_marker(self, project_id: str, epoch: str, task_count: int) -> None:
        """Write provider-free collection identity and expected-count metadata."""
        self.qdrant.upsert(
            self.ensure_task_collection(project_id),
            points=[
                models.PointStruct(
                    id=self.task_marker_id(project_id),
                    vector=[1.0, *([0.0] * (self.dimension - 1))],
                    payload={
                        "record_type": "task_index_marker",
                        "project_id": project_id,
                        "epoch": epoch,
                        "task_count": task_count,
                    },
                )
            ],
            wait=True,
        )

    def upsert(self, project_id: str, memory_id: str, text: str, payload: dict) -> None:
        collection = self.ensure_collection(project_id)
        vector = self.embed([text])[0]
        self.qdrant.upsert(
            collection,
            points=[models.PointStruct(id=memory_id, vector=vector, payload=payload)],
        )

    def upsert_observed(
        self, project_id: str, memory_id: str, text: str, payload: dict
    ) -> VectorUpsertResult:
        collection = self.ensure_collection(project_id)
        embedding = self.embed_observed([text])
        started = perf_counter()
        try:
            self.qdrant.upsert(
                collection,
                points=[
                    models.PointStruct(id=memory_id, vector=embedding.vectors[0], payload=payload)
                ],
            )
        except Exception as exc:
            raise VectorOperationError(
                str(exc),
                embedding=embedding,
                vector_store_duration_ms=round((perf_counter() - started) * 1000),
            ) from exc
        return VectorUpsertResult(
            embedding=embedding,
            vector_store_duration_ms=round((perf_counter() - started) * 1000),
        )

    def upsert_task_observed(
        self, project_id: str, task_id: str, text: str, payload: dict
    ) -> VectorUpsertResult:
        collection = self.ensure_task_collection(project_id)
        embedding = self.embed_observed([text])
        started = perf_counter()
        try:
            self.qdrant.upsert(
                collection,
                points=[
                    models.PointStruct(id=task_id, vector=embedding.vectors[0], payload=payload)
                ],
                wait=True,
            )
        except Exception as exc:
            raise VectorOperationError(
                str(exc),
                embedding=embedding,
                vector_store_duration_ms=round((perf_counter() - started) * 1000),
            ) from exc
        return VectorUpsertResult(
            embedding=embedding,
            vector_store_duration_ms=round((perf_counter() - started) * 1000),
        )

    def task_point(self, project_id: str, task_id: str) -> dict | None:
        points = self.qdrant.retrieve(
            collection_name=self.ensure_task_collection(project_id),
            ids=[task_id],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        point = points[0]
        return {"id": str(point.id), **(point.payload or {})}

    def update_task_payload(
        self, project_id: str, task_id: str, payload: dict
    ) -> VectorPayloadUpdateResult:
        started = perf_counter()
        try:
            self.qdrant.set_payload(
                collection_name=self.ensure_task_collection(project_id),
                payload=payload,
                points=[task_id],
                wait=True,
            )
        except Exception as exc:
            raise VectorOperationError(
                str(exc),
                vector_store_duration_ms=round((perf_counter() - started) * 1000),
            ) from exc
        return VectorPayloadUpdateResult(
            vector_store_duration_ms=round((perf_counter() - started) * 1000)
        )

    def task_inventory(self, project_id: str) -> dict[str, dict]:
        collection = self.ensure_task_collection(project_id)
        result: dict[str, dict] = {}
        offset = None
        while True:
            points, offset = self.qdrant.scroll(
                collection_name=collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            result.update(
                {
                    str(point.id): point.payload or {}
                    for point in points
                    if (point.payload or {}).get("record_type") != "task_index_marker"
                }
            )
            if offset is None:
                return result

    def delete_task_points(self, project_id: str, task_ids: list[str]) -> None:
        """Remove derived task points absent from authoritative PostgreSQL."""
        if not task_ids:
            return
        self.qdrant.delete(
            collection_name=self.ensure_task_collection(project_id),
            points_selector=models.PointIdsList(points=task_ids),
            wait=True,
        )

    def reset_task_collection(self, project_id: str) -> str:
        name = self.task_collection(project_id)
        existing = {item.name for item in self.qdrant.get_collections().collections}
        if name in existing:
            self.qdrant.delete_collection(name)
        return self.ensure_task_collection(project_id)

    def search_tasks_observed(
        self,
        project_id: str,
        query: str,
        limit: int,
        *,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        epic_id: str | None = None,
        label: str | None = None,
        placement: str = "all",
        sprint_ids: list[str] | None = None,
        include_unsprinted: bool = True,
    ) -> VectorSearchResult:
        # API search verifies collection identity and exact cardinality first;
        # another existence/list round-trip here would add no race safety.
        collection = self.task_collection(project_id)
        embedding = self.embed_observed([query], query=True)
        conditions: list[models.Condition] = [
            models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id)),
            models.FieldCondition(key="record_type", match=models.MatchValue(value="task")),
        ]
        if statuses:
            conditions.append(
                models.FieldCondition(key="status", match=models.MatchAny(any=statuses))
            )
        if kinds:
            conditions.append(models.FieldCondition(key="kind", match=models.MatchAny(any=kinds)))
        if epic_id:
            conditions.append(
                models.FieldCondition(key="epic_id", match=models.MatchValue(value=epic_id))
            )
        if label:
            conditions.append(
                models.FieldCondition(key="labels", match=models.MatchValue(value=label))
            )
        unsprinted = models.IsEmptyCondition(is_empty=models.PayloadField(key="sprint_id"))
        if placement == "backlog":
            conditions.extend([
                unsprinted,
                models.FieldCondition(key="kind", match=models.MatchValue(value="task")),
                models.FieldCondition(key="status", match=models.MatchAny(any=["todo", "in_progress", "blocked"])),
            ])
        if sprint_ids is not None or placement == "archive":
            alternatives: list[models.Condition] = [models.FieldCondition(
                key="sprint_id", match=models.MatchAny(any=sprint_ids or ["__no_sprint_match__"]),
            )]
            if placement == "archive" and include_unsprinted:
                alternatives.append(models.Filter(must=[
                    unsprinted,
                    models.FieldCondition(key="status", match=models.MatchAny(any=["done", "cancelled"])),
                ]))
            conditions.append(models.Filter(should=alternatives))
        started = perf_counter()
        try:
            result = self.qdrant.query_points(
                collection_name=collection,
                query=embedding.vectors[0],
                limit=limit,
                query_filter=models.Filter(must=conditions),
                with_payload=True,
                with_vectors=False,
            ).points
        except Exception as exc:
            raise VectorOperationError(
                str(exc),
                embedding=embedding,
                vector_store_duration_ms=round((perf_counter() - started) * 1000),
            ) from exc
        return VectorSearchResult(
            items=[
                {"id": str(point.id), "score": point.score, **(point.payload or {})}
                for point in result
            ],
            embedding=embedding,
            vector_store_duration_ms=round((perf_counter() - started) * 1000),
        )

    def inventory(self, project_id: str) -> dict[str, dict]:
        """Return every indexed point payload for post-restore reconciliation."""
        collection = self.ensure_collection(project_id)
        result: dict[str, dict] = {}
        offset = None
        while True:
            points, offset = self.qdrant.scroll(
                collection_name=collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            result.update({str(point.id): point.payload or {} for point in points})
            if offset is None:
                return result

    def delete_points(self, project_id: str, memory_ids: list[str]) -> None:
        """Delete points that are absent from the restored PostgreSQL source of truth."""
        if not memory_ids:
            return
        self.qdrant.delete(
            collection_name=self.ensure_collection(project_id),
            points_selector=models.PointIdsList(points=memory_ids),
            wait=True,
        )

    def reset_collection(self, project_id: str) -> str:
        """Recreate the current collection before a complete PostgreSQL reindex."""
        name = self.collection(project_id)
        existing = {item.name for item in self.qdrant.get_collections().collections}
        if name in existing:
            self.qdrant.delete_collection(name)
        return self.ensure_collection(project_id)

    def search(self, project_id: str, query: str, limit: int) -> list[dict]:
        collection = self.ensure_collection(project_id)
        vector = self.embed([query], query=True)[0]
        result = self.qdrant.query_points(
            collection_name=collection,
            query=vector,
            limit=limit,
            query_filter=models.Filter(
                must=[models.FieldCondition(key="status", match=models.MatchValue(value="active"))]
            ),
        ).points
        return [{"id": str(p.id), "score": p.score, **(p.payload or {})} for p in result]

    def search_observed(self, project_id: str, query: str, limit: int) -> VectorSearchResult:
        collection = self.ensure_collection(project_id)
        embedding = self.embed_observed([query], query=True)
        started = perf_counter()
        try:
            result = self.qdrant.query_points(
                collection_name=collection,
                query=embedding.vectors[0],
                limit=limit,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="status", match=models.MatchValue(value="active"))
                    ]
                ),
            ).points
        except Exception as exc:
            raise VectorOperationError(
                str(exc),
                embedding=embedding,
                vector_store_duration_ms=round((perf_counter() - started) * 1000),
            ) from exc
        return VectorSearchResult(
            items=[
                {"id": str(point.id), "score": point.score, **(point.payload or {})}
                for point in result
            ],
            embedding=embedding,
            vector_store_duration_ms=round((perf_counter() - started) * 1000),
        )


@lru_cache
def local_model(model_name: str):
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError(
            "Local embeddings require the optional dDuo Solo Founder 'local' dependency extra."
        ) from exc

    return TextEmbedding(model_name=model_name)


@lru_cache
def embedding_service() -> EmbeddingService:
    return EmbeddingService()
