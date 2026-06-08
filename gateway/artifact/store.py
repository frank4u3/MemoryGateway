import json
import uuid
from datetime import datetime
from typing import Optional

from gateway.artifact.schemas import (
    Artifact,
    ArtifactSearchResult,
    ArtifactType,
)
from gateway.logger import get_logger

logger = get_logger()


class ArtifactStore:
    """Redis-backed store for reusable artifacts with optional Qdrant vector search."""

    KEY_PREFIX = "artifact:"
    KEY_IDS = "artifact:ids"
    KEY_TYPE_PREFIX = "artifact:type:"
    QDRANT_COLLECTION = "artifact_index"

    def __init__(self, redis_client=None, index_store=None):
        self._redis = redis_client
        self._index_store = index_store

    @staticmethod
    def _key_for(id_: str) -> str:
        return f"{ArtifactStore.KEY_PREFIX}{id_}"

    @staticmethod
    def _type_key(type_: ArtifactType) -> str:
        return f"{ArtifactStore.KEY_TYPE_PREFIX}{type_.value}"

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:16]

    def _serialize(self, artifact: Artifact) -> str:
        return artifact.model_dump_json()

    def _deserialize(self, raw: str) -> Optional[Artifact]:
        try:
            data = json.loads(raw)
            return Artifact(**data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("artifact_deserialize_error", extra={"error": str(exc)})
            return None

    def _index_in_qdrant(self, artifact: Artifact):
        if self._index_store is None:
            return
        try:
            from gateway.indexer.schemas import CodeSymbol

            symbol = CodeSymbol(
                file_path=f"artifact://{artifact.id}",
                symbol_name=artifact.title,
                symbol_type=artifact.type.value,
                summary=f"[{artifact.creator_agent}] {artifact.content[:500]}",
                language="markdown",
            )
            self._index_store.index_symbols(
                [symbol], collection=self.QDRANT_COLLECTION
            )
        except Exception as exc:
            logger.warning("artifact_qdrant_index_error", extra={"error": str(exc)})

    def _remove_from_qdrant(self, artifact_id: str):
        if self._index_store is None:
            return
        try:
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue

            self._index_store._client.delete(
                collection_name=self.QDRANT_COLLECTION,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="file_path",
                            match=MatchValue(value=f"artifact://{artifact_id}"),
                        )
                    ]
                ),
            )
        except Exception as exc:
            logger.warning("artifact_qdrant_remove_error", extra={"error": str(exc)})

    async def save(self, artifact: Artifact) -> bool:
        if self._redis is None:
            return False
        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(artifact.id), self._serialize(artifact))
            pipe.sadd(self.KEY_IDS, artifact.id)
            pipe.sadd(self._type_key(artifact.type), artifact.id)
            await pipe.execute()
            self._index_in_qdrant(artifact)
            return True
        except Exception as exc:
            logger.error("artifact_save_error", extra={"error": str(exc)})
            return False

    async def get(self, id_: str) -> Optional[Artifact]:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._key_for(id_))
            if raw is None:
                return None
            return self._deserialize(raw)
        except Exception as exc:
            logger.error("artifact_get_error", extra={"error": str(exc)})
            return None

    async def update(self, id_: str, **updates) -> Optional[Artifact]:
        artifact = await self.get(id_)
        if artifact is None:
            return None

        old_type = artifact.type

        for key, value in updates.items():
            if value is not None and hasattr(artifact, key):
                setattr(artifact, key, value)

        artifact.version += 1
        artifact.updated_at = self._now()

        if self._redis is None:
            return artifact

        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(artifact.id), self._serialize(artifact))
            if old_type != artifact.type:
                pipe.srem(self._type_key(old_type), artifact.id)
                pipe.sadd(self._type_key(artifact.type), artifact.id)
            await pipe.execute()
            self._index_in_qdrant(artifact)
            return artifact
        except Exception as exc:
            logger.error("artifact_update_error", extra={"error": str(exc)})
            return None

    async def delete(self, id_: str) -> bool:
        artifact = await self.get(id_)
        if artifact is None:
            return False
        if self._redis is None:
            return False
        try:
            pipe = self._redis.pipeline()
            pipe.delete(self._key_for(id_))
            pipe.srem(self.KEY_IDS, id_)
            pipe.srem(self._type_key(artifact.type), id_)
            await pipe.execute()
            self._remove_from_qdrant(id_)
            return True
        except Exception as exc:
            logger.error("artifact_delete_error", extra={"error": str(exc)})
            return False

    async def search(
        self,
        query: str,
        type_filter: Optional[ArtifactType] = None,
        top_k: int = 10,
        use_semantic: bool = True,
    ) -> list[ArtifactSearchResult]:
        if self._redis is None:
            return []

        try:
            ids = await self._redis.smembers(self.KEY_IDS)
        except Exception as exc:
            logger.error("artifact_search_ids_error", extra={"error": str(exc)})
            return []

        artifacts: list[Artifact] = []
        for id_ in ids:
            artifact = await self.get(id_)
            if artifact is None:
                continue
            if type_filter and artifact.type != type_filter:
                continue
            artifacts.append(artifact)

        q = query.lower().strip()
        if not q:
            return []

        scored: list[tuple[float, Artifact]] = []
        for a in artifacts:
            score = self._score_artifact(a, q)
            if score > 0:
                scored.append((score, a))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        results = [
            ArtifactSearchResult(
                id=a.id,
                type=a.type,
                title=a.title,
                content=a.content,
                creator_agent=a.creator_agent,
                git_commit=a.git_commit,
                tags=a.tags,
                project=a.project,
                version=a.version,
                score=round(s, 4),
            )
            for s, a in top
        ]

        if use_semantic and self._index_store is not None:
            try:
                semantic_results = self._index_store.search(
                    query=q,
                    top_k=top_k,
                    collection=self.QDRANT_COLLECTION,
                )
                seen_ids = {r.id for r in results}
                for sr in semantic_results:
                    artifact_id = sr.file_path.replace("artifact://", "")
                    if artifact_id in seen_ids:
                        continue
                    artifact = await self.get(artifact_id)
                    if artifact is None:
                        continue
                    results.append(
                        ArtifactSearchResult(
                            id=artifact.id,
                            type=artifact.type,
                            title=artifact.title,
                            content=artifact.content,
                            creator_agent=artifact.creator_agent,
                            git_commit=artifact.git_commit,
                            tags=artifact.tags,
                            project=artifact.project,
                            version=artifact.version,
                            score=round(sr.score, 4),
                        )
                    )
                    seen_ids.add(artifact_id)
                results.sort(key=lambda r: r.score, reverse=True)
                results = results[:top_k]
            except Exception as exc:
                logger.warning("artifact_semantic_search_error", extra={"error": str(exc)})

        return results

    @staticmethod
    def _score_artifact(artifact: Artifact, query_lower: str) -> float:
        score = 0.0
        query_words = query_lower.split()

        title_lower = artifact.title.lower()
        content_lower = artifact.content.lower()
        tags_lower = [t.lower() for t in artifact.tags]
        creator_lower = artifact.creator_agent.lower()
        project_lower = (artifact.project or "").lower()

        if query_lower == title_lower:
            score += 10.0
        elif query_lower in title_lower:
            score += 5.0

        for tag in tags_lower:
            if query_lower == tag:
                score += 8.0
            elif query_lower in tag:
                score += 4.0

        if query_lower in content_lower:
            score += 3.0

        if query_lower == creator_lower:
            score += 7.0
        elif query_lower in creator_lower:
            score += 3.0

        if project_lower and (query_lower == project_lower or query_lower in project_lower):
            score += 6.0

        for w in query_words:
            if w == title_lower:
                score += 6.0
            elif len(w) > 2 and w in title_lower:
                score += 3.0
            if len(w) > 2 and w in content_lower:
                score += 1.0
            if len(w) > 2 and w in creator_lower:
                score += 2.0
            if project_lower and len(w) > 2 and w in project_lower:
                score += 2.0
            for tag in tags_lower:
                if w == tag:
                    score += 4.0

        return score

    async def count(self, type_filter: Optional[ArtifactType] = None) -> int:
        if self._redis is None:
            return 0
        try:
            if type_filter:
                return await self._redis.scard(self._type_key(type_filter))
            return await self._redis.scard(self.KEY_IDS)
        except Exception as exc:
            logger.error("artifact_count_error", extra={"error": str(exc)})
            return 0

    async def list_ids(self, type_filter: Optional[ArtifactType] = None) -> list[str]:
        if self._redis is None:
            return []
        try:
            if type_filter:
                raw = await self._redis.smembers(self._type_key(type_filter))
            else:
                raw = await self._redis.smembers(self.KEY_IDS)
            return sorted(raw)
        except Exception as exc:
            logger.error("artifact_list_error", extra={"error": str(exc)})
            return []

    async def clear_all(self):
        if self._redis is None:
            return
        try:
            ids_raw = await self._redis.smembers(self.KEY_IDS) or set()
            pipe = self._redis.pipeline()
            for id_ in ids_raw:
                pipe.delete(self._key_for(id_))
            pipe.delete(self.KEY_IDS)
            for t in ArtifactType:
                pipe.delete(self._type_key(t))
            await pipe.execute()
        except Exception as exc:
            logger.error("artifact_clear_error", extra={"error": str(exc)})
