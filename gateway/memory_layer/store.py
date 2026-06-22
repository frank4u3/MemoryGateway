import json
import math
import uuid
from datetime import datetime
from typing import Optional

from gateway.artifact.store import ArtifactStore
from gateway.context.store import ContextStore
from gateway.indexer.qdrant_store import CodeIndexStore
from gateway.logger import get_logger
from gateway.memory.store import MemoryStore
from gateway.memory_layer.schemas import (
    MemoryPermission,
    MemoryRecord,
    MemoryScope,
    MemorySearchResult,
    SourceType,
)

logger = get_logger()

AGENT_IDS = {"hermes", "opencode", "qoder", "vscode"}

DECAY_HALF_LIFE_DAYS = 90.0
DECAY_LAMBDA = math.log(2) / DECAY_HALF_LIFE_DAYS


class MemoryLayerStore:
    """Unified permission-scoped access layer over Context, Artifact, Indexer, and Memory Pack stores."""

    KEY_PREFIX = "mem_layer:record:"
    KEY_IDS = "mem_layer:ids"
    KEY_SCOPE_PREFIX = "mem_layer:scope:"
    KEY_SOURCE_PREFIX = "mem_layer:source:"
    KEY_AGENT_PREFIX = "mem_layer:agent:"

    def __init__(
        self,
        redis_client=None,
        context_store: Optional[ContextStore] = None,
        artifact_store: Optional[ArtifactStore] = None,
        index_store: Optional[CodeIndexStore] = None,
        memory_store: Optional[MemoryStore] = None,
    ):
        self._redis = redis_client
        self._context_store = context_store
        self._artifact_store = artifact_store
        self._index_store = index_store
        self._memory_store = memory_store

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key_for(id_: str) -> str:
        return f"{MemoryLayerStore.KEY_PREFIX}{id_}"

    @staticmethod
    def _scope_key(scope: MemoryScope, value: str) -> str:
        return f"{MemoryLayerStore.KEY_SCOPE_PREFIX}{scope.value}:{value or 'global'}"

    @staticmethod
    def _source_key(source_type: SourceType, source_id: str) -> str:
        return f"{MemoryLayerStore.KEY_SOURCE_PREFIX}{source_type.value}:{source_id}"

    @staticmethod
    def _agent_key(agent_id: str) -> str:
        return f"{MemoryLayerStore.KEY_AGENT_PREFIX}{agent_id}"

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:16]

    def _serialize(self, record: MemoryRecord) -> str:
        return record.model_dump_json()

    def _deserialize(self, raw: str) -> Optional[MemoryRecord]:
        try:
            data = json.loads(raw)
            return MemoryRecord(**data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("memory_layer_deserialize_error", extra={"error": str(exc)})
            return None

    @staticmethod
    def _compute_decay(created_at: str) -> float:
        try:
            created = datetime.strptime(created_at[:10], "%Y-%m-%d")
            days = (datetime.utcnow() - created).days
            return math.exp(-DECAY_LAMBDA * max(days, 0))
        except Exception:
            return 1.0

    def _final_score(self, record: MemoryRecord, keyword_score: float) -> float:
        recency = min(1.0, max(0.1, record.decay_score))
        importance = max(0.1, record.importance_score)
        boost = min(2.0, 1.0 + (record.access_count * 0.05))
        return round(keyword_score * importance * recency * boost, 4)

    async def record_access(self, record_id: str, agent_id: str):
        if self._redis is None:
            return
        try:
            raw = await self._redis.get(self._key_for(record_id))
            if raw is None:
                return
            record = self._deserialize(raw)
            if record is None:
                return
            record.access_count += 1
            record.last_accessed = self._now()
            record.decay_score = self._compute_decay(record.created_at)
            await self._redis.set(self._key_for(record_id), self._serialize(record))
        except Exception as exc:
            logger.warning("memory_layer_access_error", extra={"error": str(exc)})

    def _check_permission(
        self, record: MemoryRecord, agent_id: str, required: MemoryPermission = MemoryPermission.read
    ) -> bool:
        if required == MemoryPermission.read and record.scope in (MemoryScope.shared, MemoryScope.project):
            return True
        if required == MemoryPermission.admin and record.scope == MemoryScope.shared:
            perm = record.permissions.get(agent_id)
            return perm == MemoryPermission.admin if perm is not None else False
        perm = record.permissions.get(agent_id)
        if perm is None:
            return False
        if required == MemoryPermission.read:
            return perm in (MemoryPermission.read, MemoryPermission.write, MemoryPermission.admin)
        if required == MemoryPermission.write:
            return perm in (MemoryPermission.write, MemoryPermission.admin)
        if required == MemoryPermission.admin:
            return perm == MemoryPermission.admin
        return False

    def _agent_has_scope_access(self, record: MemoryRecord, agent_id: str) -> bool:
        if record.scope == MemoryScope.shared:
            return True
        if record.scope == MemoryScope.project:
            return True
        if record.scope == MemoryScope.agent:
            return record.scope_value == agent_id or self._check_permission(record, agent_id)
        return False

    def _grant_agent_access(self, record_id: str, agent_id: str):
        if self._redis is None:
            return
        try:
            self._redis.sadd(self._agent_key(agent_id), record_id)
        except Exception as exc:
            logger.warning("memory_layer_grant_error", extra={"error": str(exc)})

    def _revoke_agent_access(self, record_id: str, agent_id: str):
        if self._redis is None:
            return
        try:
            self._redis.srem(self._agent_key(agent_id), record_id)
        except Exception as exc:
            logger.warning("memory_layer_revoke_error", extra={"error": str(exc)})

    # ------------------------------------------------------------------
    # CRUD for memory records
    # ------------------------------------------------------------------

    async def share(
        self,
        source_type: SourceType,
        source_id: str,
        scope: MemoryScope,
        scope_value: str = "",
        permissions: Optional[dict[str, MemoryPermission]] = None,
        creator_agent: str = "",
    ) -> Optional[str]:
        if self._redis is None:
            return None

        existing = await self._find_by_source(source_type, source_id)
        if existing is not None:
            logger.info(
                "memory_layer_already_shared",
                extra={"source_type": source_type.value, "source_id": source_id, "existing_id": existing.id},
            )
            return existing.id

        resolved_title = source_id
        resolved_summary = source_id
        resolved_project = None
        resolved_agent = creator_agent
        tags: list[str] = []

        if source_type == SourceType.context and self._context_store is not None:
            block = await self._context_store.get(source_id)
            if block is not None:
                resolved_title = block.title
                resolved_summary = block.content[:200]
                resolved_project = None
                resolved_agent = block.source or creator_agent
                tags = block.tags

        elif source_type == SourceType.artifact and self._artifact_store is not None:
            artifact = await self._artifact_store.get(source_id)
            if artifact is not None:
                resolved_title = artifact.title
                resolved_summary = artifact.content[:200]
                resolved_project = artifact.project
                resolved_agent = artifact.creator_agent
                tags = artifact.tags

        now = self._now()
        record_id = self._generate_id()
        perm_map = permissions or {}
        if scope == MemoryScope.agent and scope_value not in perm_map:
            perm_map[scope_value] = MemoryPermission.admin
        if creator_agent and creator_agent not in perm_map:
            perm_map[creator_agent] = MemoryPermission.admin

        record = MemoryRecord(
            id=record_id,
            title=resolved_title,
            summary=resolved_summary,
            scope=scope,
            scope_value=scope_value,
            permissions=perm_map,
            source_type=source_type,
            source_id=source_id,
            source_project=resolved_project,
            source_agent=resolved_agent,
            tags=tags,
            creator_agent=creator_agent or resolved_agent,
            created_at=now,
            updated_at=now,
            decay_score=1.0,
            importance_score=1.0,
            access_count=0,
            last_accessed="",
        )

        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(record_id), self._serialize(record))
            pipe.sadd(self.KEY_IDS, record_id)
            pipe.sadd(self._scope_key(scope, scope_value), record_id)
            pipe.set(self._source_key(source_type, source_id), record_id)
            for aid in self._resolve_affected_agents(record):
                pipe.sadd(self._agent_key(aid), record_id)
            await pipe.execute()
            return record_id
        except Exception as exc:
            logger.error("memory_layer_share_error", extra={"error": str(exc)})
            return None

    def _resolve_affected_agents(self, record: MemoryRecord) -> list[str]:
        agents: set[str] = set()
        if record.scope == MemoryScope.shared:
            agents.update(AGENT_IDS)
        elif record.scope == MemoryScope.project:
            agents.update(AGENT_IDS)
        elif record.scope == MemoryScope.agent:
            agents.add(record.scope_value)
        for aid in record.permissions:
            agents.add(aid)
        return list(agents)

    async def create_inline(
        self,
        title: str,
        summary: str,
        content: str,
        scope: MemoryScope,
        scope_value: str = "",
        permissions: Optional[dict[str, MemoryPermission]] = None,
        tags: Optional[list[str]] = None,
        creator_agent: str = "",
        project: Optional[str] = None,
    ) -> Optional[str]:
        if self._redis is None:
            return None

        now = self._now()
        record_id = self._generate_id()
        perm_map = permissions or {}
        if scope == MemoryScope.agent and scope_value not in perm_map:
            perm_map[scope_value] = MemoryPermission.admin
        if creator_agent and creator_agent not in perm_map:
            perm_map[creator_agent] = MemoryPermission.admin

        record = MemoryRecord(
            id=record_id,
            title=title,
            summary=summary,
            scope=scope,
            scope_value=scope_value,
            permissions=perm_map,
            source_type=SourceType.inline,
            source_id=record_id,
            source_project=project,
            source_agent=creator_agent,
            tags=tags or [],
            creator_agent=creator_agent,
            created_at=now,
            updated_at=now,
            decay_score=1.0,
            importance_score=1.0,
            access_count=0,
            last_accessed="",
        )
        inline_key = f"mem_layer:inline:{record_id}"

        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(record_id), self._serialize(record))
            pipe.set(inline_key, content)
            pipe.sadd(self.KEY_IDS, record_id)
            pipe.sadd(self._scope_key(scope, scope_value), record_id)
            for aid in self._resolve_affected_agents(record):
                pipe.sadd(self._agent_key(aid), record_id)
            await pipe.execute()
            return record_id
        except Exception as exc:
            logger.error("memory_layer_create_error", extra={"error": str(exc)})
            return None

    async def get_inline_content(self, record_id: str) -> Optional[str]:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(f"mem_layer:inline:{record_id}")
            return raw
        except Exception:
            return None

    async def _find_by_source(self, source_type: SourceType, source_id: str) -> Optional[MemoryRecord]:
        if self._redis is None:
            return None
        try:
            record_id = await self._redis.get(self._source_key(source_type, source_id))
            if record_id is None:
                return None
            return await self._get_record(record_id)
        except Exception:
            return None

    async def _get_record(self, record_id: str) -> Optional[MemoryRecord]:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._key_for(record_id))
            if raw is None:
                return None
            return self._deserialize(raw)
        except Exception as exc:
            logger.error("memory_layer_get_error", extra={"error": str(exc)})
            return None

    async def get_record(self, record_id: str) -> Optional[MemoryRecord]:
        record = await self._get_record(record_id)
        if record is not None:
            await self.record_access(record_id, "")
        return record

    async def update_permissions(
        self, record_id: str, permissions: dict[str, MemoryPermission], agent_id: str
    ) -> Optional[MemoryRecord]:
        record = await self._get_record(record_id)
        if record is None:
            return None
        if not self._check_permission(record, agent_id, MemoryPermission.admin):
            logger.warning("memory_layer_permission_denied", extra={"agent_id": agent_id, "record_id": record_id})
            return None

        old_agents = set(self._resolve_affected_agents(record))
        record.permissions = permissions
        record.updated_at = self._now()
        new_agents = set(self._resolve_affected_agents(record))

        if self._redis is None:
            return record

        try:
            pipe = self._redis.pipeline()
            pipe.set(self._key_for(record_id), self._serialize(record))
            for aid in old_agents - new_agents:
                pipe.srem(self._agent_key(aid), record_id)
            for aid in new_agents - old_agents:
                pipe.sadd(self._agent_key(aid), record_id)
            await pipe.execute()
            return record
        except Exception as exc:
            logger.error("memory_layer_permissions_error", extra={"error": str(exc)})
            return None

    async def delete_record(self, record_id: str, agent_id: str) -> bool:
        record = await self._get_record(record_id)
        if record is None:
            return False
        if not self._check_permission(record, agent_id, MemoryPermission.admin):
            return False

        if self._redis is None:
            return False

        try:
            pipe = self._redis.pipeline()
            pipe.delete(self._key_for(record_id))
            pipe.srem(self.KEY_IDS, record_id)
            pipe.srem(self._scope_key(record.scope, record.scope_value), record_id)
            if record.source_id:
                pipe.delete(self._source_key(record.source_type, record.source_id))
            if record.source_type == SourceType.inline:
                pipe.delete(f"mem_layer:inline:{record_id}")
            for aid in self._resolve_affected_agents(record):
                pipe.srem(self._agent_key(aid), record_id)
            await pipe.execute()
            return True
        except Exception as exc:
            logger.error("memory_layer_delete_error", extra={"error": str(exc)})
            return False

    # ------------------------------------------------------------------
    # Unified search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        agent_id: str,
        scope_filter: Optional[MemoryScope] = None,
        source_filter: Optional[SourceType] = None,
        project_filter: Optional[str] = None,
        top_k: int = 20,
    ) -> list[MemorySearchResult]:
        if self._redis is None:
            return []

        record_ids = await self._redis.smembers(self.KEY_IDS)
        if not record_ids:
            return []

        results: list[MemorySearchResult] = []
        q = query.lower().strip()

        for rid in record_ids:
            record = await self._get_record(rid)
            if record is None:
                continue

            if not self._agent_has_scope_access(record, agent_id):
                continue
            if scope_filter and record.scope != scope_filter:
                continue
            if source_filter and record.source_type != source_filter:
                continue
            if project_filter and record.source_project != project_filter:
                continue

            kw_score = self._score_record(record, q) if q else 1.0
            if q and kw_score <= 0:
                continue

            fin_score = self._final_score(record, kw_score)

            results.append(
                MemorySearchResult(
                    id=record.id,
                    title=record.title,
                    summary=record.summary,
                    scope=record.scope,
                    scope_value=record.scope_value,
                    source_type=record.source_type,
                    source_id=record.source_id,
                    source_project=record.source_project,
                    source_agent=record.source_agent,
                    tags=record.tags,
                    creator_agent=record.creator_agent,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    score=round(fin_score, 4),
                    decay_score=round(record.decay_score, 4),
                    importance_score=round(record.importance_score, 4),
                    access_count=record.access_count,
                    last_accessed=record.last_accessed,
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _score_record(record: MemoryRecord, query_lower: str) -> float:
        score = 0.0
        query_words = query_lower.split()

        title_lower = record.title.lower()
        summary_lower = record.summary.lower()
        tags_lower = [t.lower() for t in record.tags]

        if query_lower == title_lower:
            score += 10.0
        elif query_lower in title_lower:
            score += 5.0

        for tag in tags_lower:
            if query_lower == tag:
                score += 8.0
            elif query_lower in tag:
                score += 4.0

        if query_lower in summary_lower:
            score += 3.0

        for w in query_words:
            if w == title_lower:
                score += 6.0
            elif len(w) > 2 and w in title_lower:
                score += 3.0
            if len(w) > 2 and w in summary_lower:
                score += 1.0
            for tag in tags_lower:
                if w == tag:
                    score += 4.0

        return score

    # ------------------------------------------------------------------
    # Agent context - all memories accessible to a given agent
    # ------------------------------------------------------------------

    async def get_agent_context(self, agent_id: str) -> list[MemorySearchResult]:
        if self._redis is None:
            return []

        record_ids = await self._redis.smembers(self.KEY_IDS)
        if not record_ids:
            return []

        results: list[MemorySearchResult] = []
        for rid in record_ids:
            record = await self._get_record(rid)
            if record is None:
                continue
            if not self._agent_has_scope_access(record, agent_id):
                continue
            results.append(
                MemorySearchResult(
                    id=record.id,
                    title=record.title,
                    summary=record.summary,
                    scope=record.scope,
                    scope_value=record.scope_value,
                    source_type=record.source_type,
                    source_id=record.source_id,
                    source_project=record.source_project,
                    source_agent=record.source_agent,
                    tags=record.tags,
                    creator_agent=record.creator_agent,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    score=1.0,
                    decay_score=round(record.decay_score, 4),
                    importance_score=round(record.importance_score, 4),
                    access_count=record.access_count,
                    last_accessed=record.last_accessed,
                )
            )
        return results

    async def get_accessible_ids(self, agent_id: str) -> list[str]:
        if self._redis is None:
            return []
        try:
            raw = await self._redis.smembers(self._agent_key(agent_id))
            return sorted(raw)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Admin helpers
    # ------------------------------------------------------------------

    async def count(self) -> int:
        if self._redis is None:
            return 0
        try:
            return await self._redis.scard(self.KEY_IDS)
        except Exception:
            return 0

    async def clear_all(self):
        if self._redis is None:
            return
        try:
            ids_raw = await self._redis.smembers(self.KEY_IDS) or set()
            pipe = self._redis.pipeline()
            for rid in ids_raw:
                record = await self._get_record(rid)
                pipe.delete(self._key_for(rid))
                if record:
                    pipe.srem(self._scope_key(record.scope, record.scope_value), rid)
                    if record.source_id:
                        pipe.delete(self._source_key(record.source_type, record.source_id))
                    if record.source_type == SourceType.inline:
                        pipe.delete(f"mem_layer:inline:{rid}")
                    for aid in self._resolve_affected_agents(record):
                        pipe.srem(self._agent_key(aid), rid)
            pipe.delete(self.KEY_IDS)
            for aid in AGENT_IDS:
                pipe.delete(self._agent_key(aid))
            await pipe.execute()
        except Exception as exc:
            logger.error("memory_layer_clear_error", extra={"error": str(exc)})
