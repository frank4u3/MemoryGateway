import json
import uuid
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from gateway.indexer.embedder import CodeEmbedder
from gateway.logger import get_logger
from gateway.semantic_cache.schemas import SemanticCacheEntry, SemanticSearchResult

logger = get_logger()

_COLLECTION = "semantic_cache"
_VECTOR_SIZE = CodeEmbedder.DIM


class SemanticCache:
    """Qdrant-backed semantic cache for chat completions.

    Stores embeddings of canonical prompt text and retrieves
    near-duplicate requests by cosine similarity.
    """

    def __init__(
        self,
        location: Optional[str] = None,
        url: Optional[str] = None,
        port: int = 6333,
        prefer_grpc: bool = False,
        embedder: Optional[CodeEmbedder] = None,
        threshold: float = 0.98,
    ):
        self._embedder = embedder or CodeEmbedder()
        self._threshold = threshold
        self._client = QdrantClient(
            location=location,
            url=url,
            port=port,
            prefer_grpc=prefer_grpc,
        )
        self._ensure_collection()

    def _ensure_collection(self):
        if not self._client.collection_exists(_COLLECTION):
            self._client.create_collection(
                collection_name=_COLLECTION,
                vectors_config=VectorParams(
                    size=_VECTOR_SIZE,
                    distance=Distance.COSINE,
                ),
            )

    def _build_embedding_text(self, entry: SemanticCacheEntry) -> str:
        parts = [entry.canonical_text]
        if entry.model:
            parts.append(f"model:{entry.model}")
        if entry.temperature is not None:
            parts.append(f"temp:{entry.temperature}")
        if entry.max_tokens is not None:
            parts.append(f"max_tokens:{entry.max_tokens}")
        if entry.top_p is not None:
            parts.append(f"top_p:{entry.top_p}")
        return "\n".join(parts)

    def store(self, entry: SemanticCacheEntry) -> bool:
        """Store a cache entry in Qdrant."""
        try:
            text = self._build_embedding_text(entry)
            embedding = self._embedder.embed(text)
            point = PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "canonical_hash": entry.canonical_hash,
                    "canonical_text": entry.canonical_text,
                    "model": entry.model,
                    "response": json.dumps(entry.response),
                },
            )
            self._client.upsert(
                collection_name=_COLLECTION,
                points=[point],
            )
            return True
        except Exception as exc:
            logger.error("semantic_cache_store_error", extra={"error": str(exc)})
            return False

    def search(
        self,
        canonical_text: str,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        threshold: Optional[float] = None,
        top_k: int = 1,
    ) -> Optional[SemanticSearchResult]:
        """Search for semantically similar cached entries.

        Returns the closest match above the threshold, or None.
        """
        try:
            effective_threshold = threshold if threshold is not None else self._threshold
            parts = [canonical_text]
            if model:
                parts.append(f"model:{model}")
            if temperature is not None:
                parts.append(f"temp:{temperature}")
            if max_tokens is not None:
                parts.append(f"max_tokens:{max_tokens}")
            if top_p is not None:
                parts.append(f"top_p:{top_p}")
            query_text = "\n".join(parts)

            embedding = self._embedder.embed(query_text)

            response = self._client.query_points(
                collection_name=_COLLECTION,
                query=embedding,
                limit=top_k,
                with_payload=True,
                score_threshold=effective_threshold,
            )

            if not response.points:
                return None

            best = response.points[0]
            if best.score < effective_threshold:
                return None

            return SemanticSearchResult(
                canonical_hash=best.payload.get("canonical_hash", ""),
                response=json.loads(best.payload.get("response", "{}")),
                score=best.score,
            )
        except Exception as exc:
            logger.error("semantic_cache_search_error", extra={"error": str(exc)})
            return None

    def clear(self):
        """Delete all points in the semantic cache collection."""
        try:
            self._client.delete_collection(_COLLECTION)
            self._ensure_collection()
        except Exception as exc:
            logger.error("semantic_cache_clear_error", extra={"error": str(exc)})

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float):
        self._threshold = value


def create_semantic_cache(
    in_memory: bool = True,
    threshold: float = 0.98,
) -> SemanticCache:
    """Factory: create a SemanticCache instance."""
    cache = SemanticCache(
        location=":memory:" if in_memory else None,
        url=None if in_memory else "localhost",
        port=6333,
        threshold=threshold,
    )
    return cache
