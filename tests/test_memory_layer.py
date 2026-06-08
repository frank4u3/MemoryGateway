import pytest
from fakeredis import FakeAsyncRedis

from gateway.memory_layer.schemas import (
    CreateMemoryRequest,
    CreateMemoryResponse,
    MemoryPermission,
    MemoryRecord,
    MemoryScope,
    MemorySearchResult,
    SearchMemoryRequest,
    SearchMemoryResponse,
    ShareMemoryRequest,
    ShareMemoryResponse,
    SourceType,
    UpdatePermissionsRequest,
    UpdatePermissionsResponse,
)
from gateway.memory_layer.store import MemoryLayerStore, AGENT_IDS


class TestMemoryLayerSchemas:
    def test_memory_scope_enum(self):
        assert MemoryScope.shared.value == "shared"
        assert MemoryScope.project.value == "project"
        assert MemoryScope.agent.value == "agent"

    def test_memory_permission_enum(self):
        assert MemoryPermission.read.value == "read"
        assert MemoryPermission.write.value == "write"
        assert MemoryPermission.admin.value == "admin"

    def test_source_type_enum(self):
        assert SourceType.context.value == "context"
        assert SourceType.artifact.value == "artifact"
        assert SourceType.indexer.value == "indexer"
        assert SourceType.memory_pack.value == "memory_pack"
        assert SourceType.inline.value == "inline"

    def test_memory_record_defaults(self):
        r = MemoryRecord(
            id="abc",
            title="Test",
            summary="A test record",
            scope=MemoryScope.shared,
            source_type=SourceType.inline,
            source_id="abc",
        )
        assert r.scope_value == ""
        assert r.permissions == {}
        assert r.tags == []
        assert r.creator_agent == ""
        assert r.created_at == ""
        assert r.updated_at == ""

    def test_memory_record_full(self):
        r = MemoryRecord(
            id="abc",
            title="Architecture Decision",
            summary="Use Redis for caching",
            scope=MemoryScope.project,
            scope_value="memory-gateway",
            permissions={"hermes": MemoryPermission.admin, "opencode": MemoryPermission.read},
            source_type=SourceType.context,
            source_id="ctx_123",
            source_project="memory-gateway",
            source_agent="hermes",
            tags=["cache", "redis"],
            creator_agent="hermes",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert r.permissions["hermes"] == MemoryPermission.admin
        assert r.source_project == "memory-gateway"

    def test_share_request(self):
        r = ShareMemoryRequest(
            source_type=SourceType.artifact,
            source_id="art_123",
            scope=MemoryScope.shared,
        )
        assert r.scope == MemoryScope.shared
        assert r.permissions is None

    def test_share_response(self):
        r = ShareMemoryResponse(id="abc", source_type=SourceType.context, source_id="ctx_1", scope=MemoryScope.project)
        assert r.message == "Memory shared"

    def test_search_request(self):
        r = SearchMemoryRequest(query="cache", agent_id="hermes")
        assert r.top_k == 20
        assert r.scope_filter is None
        assert r.source_filter is None

    def test_search_result_defaults(self):
        r = MemorySearchResult(
            id="abc", title="T", summary="S", scope=MemoryScope.shared, source_type=SourceType.inline, source_id="abc",
        )
        assert r.score == 0.0
        assert r.tags == []

    def test_search_response(self):
        results = [MemorySearchResult(id="a", title="A", summary="S", scope=MemoryScope.shared, source_type=SourceType.inline, source_id="a")]
        r = SearchMemoryResponse(results=results, query="test", agent_id="hermes", total_hits=1)
        assert r.total_hits == 1

    def test_update_permissions_request(self):
        r = UpdatePermissionsRequest(
            record_id="abc",
            permissions={"opencode": MemoryPermission.admin},
            agent_id="hermes",
        )
        assert r.permissions["opencode"] == MemoryPermission.admin

    def test_update_permissions_response(self):
        r = UpdatePermissionsResponse(id="abc")
        assert r.message == "Permissions updated"


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def layer(redis):
    s = MemoryLayerStore(redis_client=redis)
    yield s


class TestMemoryLayerShare:
    @pytest.mark.asyncio
    async def test_share_inline_sets_redis(self, layer):
        rid = await layer.create_inline(
            title="Test Memory",
            summary="A test",
            content="Test content body",
            scope=MemoryScope.shared,
            creator_agent="hermes",
        )
        assert rid is not None
        assert len(rid) == 16
        count = await layer.count()
        assert count == 1

    @pytest.mark.asyncio
    async def test_share_inline_returns_content(self, layer):
        rid = await layer.create_inline(
            title="Test",
            summary="A test",
            content="The content body",
            scope=MemoryScope.shared,
            creator_agent="hermes",
        )
        content = await layer.get_inline_content(rid)
        assert content == "The content body"

    @pytest.mark.asyncio
    async def test_share_adds_to_scope_set(self, layer):
        rid = await layer.create_inline(
            title="Project Memo",
            summary="Project scoped",
            content="Secret project data",
            scope=MemoryScope.project,
            scope_value="my-project",
            creator_agent="opencode",
        )
        record = await layer.get_record(rid)
        assert record is not None
        assert record.scope == MemoryScope.project
        assert record.scope_value == "my-project"

    @pytest.mark.asyncio
    async def test_share_agent_scope_grants_admin(self, layer):
        rid = await layer.create_inline(
            title="Private",
            summary="Agent private",
            content="Only for hermes",
            scope=MemoryScope.agent,
            scope_value="hermes",
            creator_agent="hermes",
        )
        record = await layer.get_record(rid)
        assert record.permissions.get("hermes") == MemoryPermission.admin

    @pytest.mark.asyncio
    async def test_share_with_custom_permissions(self, layer):
        rid = await layer.create_inline(
            title="Shared with opencode",
            summary="Permission test",
            content="Content",
            scope=MemoryScope.shared,
            permissions={"opencode": MemoryPermission.write},
            creator_agent="hermes",
        )
        record = await layer.get_record(rid)
        assert record.permissions["opencode"] == MemoryPermission.write

    @pytest.mark.asyncio
    async def test_share_no_redis_returns_none(self):
        s = MemoryLayerStore(redis_client=None)
        rid = await s.share(SourceType.inline, "x", MemoryScope.shared)
        assert rid is None

    @pytest.mark.asyncio
    async def test_get_record_missing(self, layer):
        record = await layer.get_record("nonexistent")
        assert record is None

    @pytest.mark.asyncio
    async def test_delete_record(self, layer):
        rid = await layer.create_inline(
            title="Delete Me",
            summary="To be deleted",
            content="Bye",
            scope=MemoryScope.shared,
            creator_agent="hermes",
        )
        assert await layer.delete_record(rid, "hermes") is True
        assert await layer.get_record(rid) is None

    @pytest.mark.asyncio
    async def test_delete_record_no_permission(self, layer):
        rid = await layer.create_inline(
            title="Private",
            summary="Agent private",
            content="Secret",
            scope=MemoryScope.agent,
            scope_value="hermes",
            creator_agent="hermes",
        )
        assert await layer.delete_record(rid, "opencode") is False

    @pytest.mark.asyncio
    async def test_delete_missing(self, layer):
        assert await layer.delete_record("nonexistent", "hermes") is False

    @pytest.mark.asyncio
    async def test_clear_all(self, layer):
        for i in range(3):
            await layer.create_inline(title=f"T{i}", summary=f"S{i}", content=f"C{i}", scope=MemoryScope.shared, creator_agent="hermes")
        await layer.clear_all()
        assert await layer.count() == 0

    @pytest.mark.asyncio
    async def test_update_permissions(self, layer):
        rid = await layer.create_inline(
            title="Perm Test",
            summary="Testing permissions",
            content="Content",
            scope=MemoryScope.shared,
            creator_agent="hermes",
        )
        updated = await layer.update_permissions(rid, {"opencode": MemoryPermission.read}, "hermes")
        assert updated is not None
        assert updated.permissions["opencode"] == MemoryPermission.read

    @pytest.mark.asyncio
    async def test_update_permissions_no_admin(self, layer):
        rid = await layer.create_inline(
            title="Perm Test",
            summary="Testing",
            content="Content",
            scope=MemoryScope.shared,
            creator_agent="hermes",
        )
        result = await layer.update_permissions(rid, {"hermes": MemoryPermission.read}, "opencode")
        assert result is None


class TestMemoryLayerSearch:
    @pytest.fixture
    async def populated_layer(self):
        r = FakeAsyncRedis(decode_responses=True)
        s = MemoryLayerStore(redis_client=r)

        await s.create_inline(
            title="Auth Middleware",
            summary="JWT authentication middleware for FastAPI",
            content="def authenticate(): pass",
            scope=MemoryScope.shared,
            tags=["auth", "security"],
            creator_agent="hermes",
            project="memory-gateway",
        )
        await s.create_inline(
            title="User API Design",
            summary="RESTful user management API with CRUD operations",
            content="GET /users, POST /users",
            scope=MemoryScope.project,
            scope_value="memory-gateway",
            tags=["rest", "users"],
            creator_agent="opencode",
            project="memory-gateway",
        )
        await s.create_inline(
            title="Code Review Prompt",
            summary="Systematic code review prompt template",
            content="Review this code:",
            scope=MemoryScope.shared,
            tags=["review", "template"],
            creator_agent="qoder",
            project="opencode",
        )
        await s.create_inline(
            title="Private Note",
            summary="Only for hermes",
            content="Personal notes",
            scope=MemoryScope.agent,
            scope_value="hermes",
            tags=["personal"],
            creator_agent="hermes",
        )
        yield s
        await r.aclose()

    @pytest.mark.asyncio
    async def test_search_by_title(self, populated_layer):
        results = await populated_layer.search("Auth Middleware", agent_id="hermes")
        assert len(results) >= 1
        assert results[0].title == "Auth Middleware"

    @pytest.mark.asyncio
    async def test_search_by_summary(self, populated_layer):
        results = await populated_layer.search("JWT", agent_id="hermes")
        assert len(results) >= 1
        assert any("JWT" in r.summary for r in results)

    @pytest.mark.asyncio
    async def test_search_by_tag(self, populated_layer):
        results = await populated_layer.search("security", agent_id="hermes")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_shared_accessible_to_all(self, populated_layer):
        results = await populated_layer.search("auth", agent_id="vscode")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_agent_scoped_only_accessible_by_owner(self, populated_layer):
        hermes_results = await populated_layer.search("Private Note", agent_id="hermes")
        assert len(hermes_results) >= 1
        opencode_results = await populated_layer.search("Private Note", agent_id="opencode")
        assert len(opencode_results) == 0

    @pytest.mark.asyncio
    async def test_search_project_scoped(self, populated_layer):
        results = await populated_layer.search("User API", agent_id="hermes")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_with_scope_filter(self, populated_layer):
        results = await populated_layer.search("", agent_id="hermes", scope_filter=MemoryScope.agent)
        assert len(results) >= 1
        for r in results:
            assert r.scope == MemoryScope.agent

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_all(self, populated_layer):
        results = await populated_layer.search("", agent_id="hermes")
        assert len(results) >= 3

    @pytest.mark.asyncio
    async def test_search_no_match(self, populated_layer):
        results = await populated_layer.search("zzz_nonexistent_zzz", agent_id="hermes")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_top_k_limits(self, populated_layer):
        results = await populated_layer.search("", agent_id="hermes", top_k=1)
        assert len(results) <= 1

    @pytest.mark.asyncio
    async def test_search_result_has_all_fields(self, populated_layer):
        results = await populated_layer.search("Auth", agent_id="hermes")
        assert len(results) >= 1
        r = results[0]
        assert r.id is not None
        assert r.title is not None
        assert r.summary is not None
        assert r.scope is not None
        assert r.source_type is not None
        assert isinstance(r.tags, list)
        assert isinstance(r.score, float)


class TestMemoryLayerAgentContext:
    @pytest.fixture
    async def context_layer(self):
        r = FakeAsyncRedis(decode_responses=True)
        s = MemoryLayerStore(redis_client=r)

        await s.create_inline(title="Shared Mem 1", summary="S1", content="C1", scope=MemoryScope.shared, creator_agent="hermes")
        await s.create_inline(title="Shared Mem 2", summary="S2", content="C2", scope=MemoryScope.shared, creator_agent="opencode")
        await s.create_inline(title="Project Mem", summary="S3", content="C3", scope=MemoryScope.project, scope_value="my-project", creator_agent="hermes")
        await s.create_inline(title="Agent Mem Hermes", summary="S4", content="C4", scope=MemoryScope.agent, scope_value="hermes", creator_agent="hermes")
        await s.create_inline(title="Agent Mem Qoder", summary="S5", content="C5", scope=MemoryScope.agent, scope_value="qoder", creator_agent="qoder")
        yield s
        await r.aclose()

    @pytest.mark.asyncio
    async def test_agent_context_includes_shared(self, context_layer):
        results = await context_layer.get_agent_context("hermes")
        titles = {r.title for r in results}
        assert "Shared Mem 1" in titles
        assert "Shared Mem 2" in titles

    @pytest.mark.asyncio
    async def test_agent_context_includes_project_scoped(self, context_layer):
        results = await context_layer.get_agent_context("hermes")
        titles = {r.title for r in results}
        assert "Project Mem" in titles

    @pytest.mark.asyncio
    async def test_agent_context_includes_own_agent_memories(self, context_layer):
        results = await context_layer.get_agent_context("hermes")
        titles = {r.title for r in results}
        assert "Agent Mem Hermes" in titles

    @pytest.mark.asyncio
    async def test_agent_context_excludes_other_agent_memories(self, context_layer):
        results = await context_layer.get_agent_context("hermes")
        titles = {r.title for r in results}
        assert "Agent Mem Qoder" not in titles

    @pytest.mark.asyncio
    async def test_agent_context_all_for_qoder(self, context_layer):
        results = await context_layer.get_agent_context("qoder")
        titles = {r.title for r in results}
        assert "Shared Mem 1" in titles
        assert "Agent Mem Qoder" in titles
        assert "Agent Mem Hermes" not in titles

    @pytest.mark.asyncio
    async def test_agent_context_no_redis(self):
        s = MemoryLayerStore(redis_client=None)
        results = await s.get_agent_context("hermes")
        assert results == []

    @pytest.mark.asyncio
    async def test_agent_context_unknown_agent_sees_shared_and_project(self, context_layer):
        results = await context_layer.get_agent_context("unknown_agent")
        assert len(results) == 3
        titles = {r.title for r in results}
        assert "Shared Mem 1" in titles
        assert "Shared Mem 2" in titles
        assert "Project Mem" in titles
        assert "Agent Mem Hermes" not in titles


class TestMemoryLayerNoRedis:
    @pytest.mark.asyncio
    async def test_search_no_redis(self):
        s = MemoryLayerStore(redis_client=None)
        results = await s.search("test", agent_id="hermes")
        assert results == []

    @pytest.mark.asyncio
    async def test_count_no_redis(self):
        s = MemoryLayerStore(redis_client=None)
        assert await s.count() == 0

    @pytest.mark.asyncio
    async def test_get_accessible_ids_no_redis(self):
        s = MemoryLayerStore(redis_client=None)
        assert await s.get_accessible_ids("hermes") == []
