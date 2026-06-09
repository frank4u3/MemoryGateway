import json
import uuid
from datetime import datetime
from typing import Optional

from gateway.learning.schemas import (
    Learning,
    LearningSearchResult,
    LearningType,
)
from gateway.logger import get_logger

logger = get_logger()


class LearningStore:
    KEY_PREFIX = "learning:"
    KEY_IDS = "learning:ids"
    KEY_TYPE_PREFIX = "learning:type:"
    QDRANT_COLLECTION = "learning_index"

    def __init__(self, redis_client=None, index_store=None):
        self._redis = redis_client
        self._index_store = index_store

    @staticmethod
    def _key_for(id_: str) -> str:
        return f"{LearningStore.KEY_PREFIX}{id_}"

    @staticmethod
    def _type_key(type_: LearningType) -> str:
        return f"{LearningStore.KEY_TYPE_PREFIX}{type_.value}"

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:16]

    def _serialize(self, learning: Learning) -> str:
        return learning.model_dump_json()

    def _deserialize(self, raw: str) -> Optional[Learning]:
        try:
            data = json.loads(raw)
            return Learning(**data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("learning_deserialize_error", extra={"error": str(exc)})
            return None

    def _index_in_qdrant(self, learning: Learning):
        if self._index_store is None:
            return
        try:
            from gateway.indexer.schemas import CodeSymbol

            symbol = CodeSymbol(
                file_path=f"learning://{learning.id}",
                symbol_name=learning.title,
                symbol_type=learning.type.value,
                summary=f"[{learning.resolved_by}] {learning.content[:500]}",
                language="markdown",
            )
            self._index_store.index_symbols(
                [symbol], collection=self.QDRANT_COLLECTION
            )
        except Exception as exc:
            logger.warning("learning_qdrant_index_error", extra={"error": str(exc)})

    def _remove_from_qdrant(self, learning_id: str):
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
                            match=MatchValue(value=f"learning://{learning_id}"),
                        )
                    ]
                ),
            )
        except Exception as exc:
            logger.warning("learning_qdrant_remove_error", extra={"error": str(exc)})

    async def save(self, learning: Learning) -> bool:
        if self._redis is None:
            return False
        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(learning.id), self._serialize(learning))
            pipe.sadd(self.KEY_IDS, learning.id)
            pipe.sadd(self._type_key(learning.type), learning.id)
            await pipe.execute()
            self._index_in_qdrant(learning)
            return True
        except Exception as exc:
            logger.error("learning_save_error", extra={"error": str(exc)})
            return False

    async def get(self, id_: str) -> Optional[Learning]:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._key_for(id_))
            if raw is None:
                return None
            return self._deserialize(raw)
        except Exception as exc:
            logger.error("learning_get_error", extra={"error": str(exc)})
            return None

    async def update(self, id_: str, **updates) -> Optional[Learning]:
        learning = await self.get(id_)
        if learning is None:
            return None

        old_type = learning.type

        for key, value in updates.items():
            if value is not None and hasattr(learning, key):
                setattr(learning, key, value)

        learning.version += 1
        learning.updated_at = self._now()

        if self._redis is None:
            return learning

        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(learning.id), self._serialize(learning))
            if old_type != learning.type:
                pipe.srem(self._type_key(old_type), learning.id)
                pipe.sadd(self._type_key(learning.type), learning.id)
            await pipe.execute()
            self._index_in_qdrant(learning)
            return learning
        except Exception as exc:
            logger.error("learning_update_error", extra={"error": str(exc)})
            return None

    async def delete(self, id_: str) -> bool:
        learning = await self.get(id_)
        if learning is None:
            return False
        if self._redis is None:
            return False
        try:
            pipe = self._redis.pipeline()
            pipe.delete(self._key_for(id_))
            pipe.srem(self.KEY_IDS, id_)
            pipe.srem(self._type_key(learning.type), id_)
            await pipe.execute()
            self._remove_from_qdrant(id_)
            return True
        except Exception as exc:
            logger.error("learning_delete_error", extra={"error": str(exc)})
            return False

    async def search(
        self,
        query: str,
        type_filter: Optional[LearningType] = None,
        top_k: int = 10,
        use_semantic: bool = True,
    ) -> list[LearningSearchResult]:
        if self._redis is None:
            return []

        try:
            ids = await self._redis.smembers(self.KEY_IDS)
        except Exception as exc:
            logger.error("learning_search_ids_error", extra={"error": str(exc)})
            return []

        learnings: list[Learning] = []
        for id_ in ids:
            learning = await self.get(id_)
            if learning is None:
                continue
            if type_filter and learning.type != type_filter:
                continue
            learnings.append(learning)

        q = query.lower().strip()
        if not q:
            return []

        scored: list[tuple[float, Learning]] = []
        for ln in learnings:
            score = self._score_learning(ln, q)
            if score > 0:
                scored.append((score, ln))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        results = [
            LearningSearchResult(
                id=ln.id,
                type=ln.type,
                title=ln.title,
                content=ln.content,
                source_issue=ln.source_issue,
                resolved_by=ln.resolved_by,
                tags=ln.tags,
                project=ln.project,
                version=ln.version,
                score=round(s, 4),
            )
            for s, ln in top
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
                    learning_id = sr.file_path.replace("learning://", "")
                    if learning_id in seen_ids:
                        continue
                    learning = await self.get(learning_id)
                    if learning is None:
                        continue
                    results.append(
                        LearningSearchResult(
                            id=learning.id,
                            type=learning.type,
                            title=learning.title,
                            content=learning.content,
                            source_issue=learning.source_issue,
                            resolved_by=learning.resolved_by,
                            tags=learning.tags,
                            project=learning.project,
                            version=learning.version,
                            score=round(sr.score, 4),
                        )
                    )
                    seen_ids.add(learning_id)
                results.sort(key=lambda r: r.score, reverse=True)
                results = results[:top_k]
            except Exception as exc:
                logger.warning("learning_semantic_search_error", extra={"error": str(exc)})

        return results

    @staticmethod
    def _score_learning(learning: Learning, query_lower: str) -> float:
        score = 0.0
        query_words = query_lower.split()

        title_lower = learning.title.lower()
        content_lower = learning.content.lower()
        tags_lower = [t.lower() for t in learning.tags]
        source_lower = learning.source_issue.lower()
        resolved_lower = learning.resolved_by.lower()
        project_lower = (learning.project or "").lower()

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

        if source_lower and query_lower in source_lower:
            score += 2.0

        if resolved_lower and (query_lower == resolved_lower or query_lower in resolved_lower):
            score += 5.0

        if project_lower and (query_lower == project_lower or query_lower in project_lower):
            score += 4.0

        for w in query_words:
            if w == title_lower:
                score += 6.0
            elif len(w) > 2 and w in title_lower:
                score += 3.0
            if len(w) > 2 and w in content_lower:
                score += 1.0
            if source_lower and len(w) > 2 and w in source_lower:
                score += 1.0
            if resolved_lower and len(w) > 2 and w in resolved_lower:
                score += 2.0
            if project_lower and len(w) > 2 and w in project_lower:
                score += 1.0
            for tag in tags_lower:
                if w == tag:
                    score += 4.0
                elif len(w) > 2 and w in tag:
                    score += 2.0

        return score

    async def count(self, type_filter: Optional[LearningType] = None) -> int:
        if self._redis is None:
            return 0
        try:
            if type_filter:
                return await self._redis.scard(self._type_key(type_filter))
            return await self._redis.scard(self.KEY_IDS)
        except Exception as exc:
            logger.error("learning_count_error", extra={"error": str(exc)})
            return 0

    async def list_ids(self, type_filter: Optional[LearningType] = None) -> list[str]:
        if self._redis is None:
            return []
        try:
            if type_filter:
                raw = await self._redis.smembers(self._type_key(type_filter))
            else:
                raw = await self._redis.smembers(self.KEY_IDS)
            return sorted(raw)
        except Exception as exc:
            logger.error("learning_list_error", extra={"error": str(exc)})
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
            for t in LearningType:
                pipe.delete(self._type_key(t))
            await pipe.execute()
        except Exception as exc:
            logger.error("learning_clear_error", extra={"error": str(exc)})
