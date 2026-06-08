import json
import uuid
from datetime import datetime
from typing import Optional

from gateway.context.schemas import (
    ContextBlock,
    ContextType,
    ContextSearchResult,
)
from gateway.logger import get_logger

logger = get_logger()


class ContextStore:
    """Redis-backed store for reusable context blocks with optional Qdrant vector search."""

    KEY_PREFIX = "context:"
    KEY_IDS = "context:ids"
    KEY_TYPE_PREFIX = "context:type:"

    def __init__(self, redis_client=None, index_store=None):
        self._redis = redis_client
        self._index_store = index_store

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key_for(id_: str) -> str:
        return f"{ContextStore.KEY_PREFIX}{id_}"

    @staticmethod
    def _type_key(type_: ContextType) -> str:
        return f"{ContextStore.KEY_TYPE_PREFIX}{type_.value}"

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:16]

    def _serialize(self, block: ContextBlock) -> str:
        return block.model_dump_json()

    def _deserialize(self, raw: str) -> Optional[ContextBlock]:
        try:
            data = json.loads(raw)
            return ContextBlock(**data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("context_deserialize_error", extra={"error": str(exc)})
            return None

    def _index_in_qdrant(self, block: ContextBlock):
        if self._index_store is None:
            return
        try:
            from gateway.indexer.schemas import CodeSymbol

            symbol = CodeSymbol(
                file_path=f"context://{block.id}",
                symbol_name=block.title,
                symbol_type="context_block",
                summary=block.content[:500],
                language="markdown",
            )
            self._index_store.index_symbols([symbol])
        except Exception as exc:
            logger.warning("context_qdrant_index_error", extra={"error": str(exc)})

    def _remove_from_qdrant(self, block_id: str):
        if self._index_store is None:
            return
        try:
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue

            self._index_store._client.delete(
                collection_name="code_index",
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="file_path",
                            match=MatchValue(value=f"context://{block_id}"),
                        )
                    ]
                ),
            )
        except Exception as exc:
            logger.warning("context_qdrant_remove_error", extra={"error": str(exc)})

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def save(self, block: ContextBlock) -> bool:
        if self._redis is None:
            return False
        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(block.id), self._serialize(block))
            pipe.sadd(self.KEY_IDS, block.id)
            pipe.sadd(self._type_key(block.type), block.id)
            await pipe.execute()
            self._index_in_qdrant(block)
            return True
        except Exception as exc:
            logger.error("context_save_error", extra={"error": str(exc)})
            return False

    async def get(self, id_: str) -> Optional[ContextBlock]:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._key_for(id_))
            if raw is None:
                return None
            return self._deserialize(raw)
        except Exception as exc:
            logger.error("context_get_error", extra={"error": str(exc)})
            return None

    async def update(self, id_: str, **updates) -> Optional[ContextBlock]:
        block = await self.get(id_)
        if block is None:
            return None

        old_type = block.type

        for key, value in updates.items():
            if value is not None and hasattr(block, key):
                setattr(block, key, value)

        block.version += 1
        block.updated_at = self._now()

        if self._redis is None:
            return block

        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(block.id), self._serialize(block))
            if old_type != block.type:
                pipe.srem(self._type_key(old_type), block.id)
                pipe.sadd(self._type_key(block.type), block.id)
            await pipe.execute()
            self._index_in_qdrant(block)
            return block
        except Exception as exc:
            logger.error("context_update_error", extra={"error": str(exc)})
            return None

    async def delete(self, id_: str) -> bool:
        block = await self.get(id_)
        if block is None:
            return False
        if self._redis is None:
            return False
        try:
            pipe = self._redis.pipeline()
            pipe.delete(self._key_for(id_))
            pipe.srem(self.KEY_IDS, id_)
            pipe.srem(self._type_key(block.type), id_)
            await pipe.execute()
            self._remove_from_qdrant(id_)
            return True
        except Exception as exc:
            logger.error("context_delete_error", extra={"error": str(exc)})
            return False

    async def search(
        self,
        query: str,
        type_filter: Optional[ContextType] = None,
        top_k: int = 10,
    ) -> list[ContextSearchResult]:
        if self._redis is None:
            return []

        try:
            ids = await self._redis.smembers(self.KEY_IDS)
        except Exception as exc:
            logger.error("context_search_ids_error", extra={"error": str(exc)})
            return []

        blocks: list[ContextBlock] = []
        for id_ in ids:
            block = await self.get(id_)
            if block is None:
                continue
            if type_filter and block.type != type_filter:
                continue
            blocks.append(block)

        q = query.lower().strip()
        if not q:
            return []
        scored: list[tuple[float, ContextBlock]] = []
        for b in blocks:
            score = self._score_block(b, q)
            if score > 0:
                scored.append((score, b))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        return [
            ContextSearchResult(
                id=b.id,
                type=b.type,
                title=b.title,
                content=b.content,
                tags=b.tags,
                source=b.source,
                version=b.version,
                score=round(s, 4),
            )
            for s, b in top
        ]

    @staticmethod
    def _score_block(block: ContextBlock, query_lower: str) -> float:
        score = 0.0
        query_words = query_lower.split()

        title_lower = block.title.lower()
        content_lower = block.content.lower()
        tags_lower = [t.lower() for t in block.tags]

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

        for w in query_words:
            if w == title_lower:
                score += 6.0
            elif len(w) > 2 and w in title_lower:
                score += 3.0
            if len(w) > 2 and w in content_lower:
                score += 1.0
            for tag in tags_lower:
                if w == tag:
                    score += 4.0

        return score

    async def count(self, type_filter: Optional[ContextType] = None) -> int:
        if self._redis is None:
            return 0
        try:
            if type_filter:
                return await self._redis.scard(self._type_key(type_filter))
            return await self._redis.scard(self.KEY_IDS)
        except Exception as exc:
            logger.error("context_count_error", extra={"error": str(exc)})
            return 0

    async def list_ids(self, type_filter: Optional[ContextType] = None) -> list[str]:
        if self._redis is None:
            return []
        try:
            if type_filter:
                raw = await self._redis.smembers(self._type_key(type_filter))
            else:
                raw = await self._redis.smembers(self.KEY_IDS)
            return sorted(raw)
        except Exception as exc:
            logger.error("context_list_error", extra={"error": str(exc)})
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
            for t in ContextType:
                pipe.delete(self._type_key(t))
            await pipe.execute()
        except Exception as exc:
            logger.error("context_clear_error", extra={"error": str(exc)})


async def register_block(
    store: ContextStore,
    type_: ContextType,
    title: str,
    content: str,
    tags: Optional[list[str]] = None,
    source: Optional[str] = None,
) -> ContextBlock:
    now = store._now()
    block = ContextBlock(
        id=store._generate_id(),
        type=type_,
        title=title,
        content=content,
        tags=tags or [],
        source=source,
        created_at=now,
        updated_at=now,
        version=1,
    )
    await store.save(block)
    return block
