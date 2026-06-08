import pytest
from fakeredis import FakeAsyncRedis

from gateway.context.schemas import (
    ContextBlock,
    ContextResponse,
    ContextSearchResult,
    ContextType,
    RegisterContextRequest,
    RegisterContextResponse,
    SearchContextRequest,
    SearchContextResponse,
    UpdateContextRequest,
    UpdateContextResponse,
)
from gateway.context.store import ContextStore, register_block

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestContextSchemas:
    def test_context_type_enum(self):
        assert ContextType.architecture.value == "architecture"
        assert ContextType.coding_standards.value == "coding_standards"
        assert ContextType.roadmap.value == "roadmap"
        assert ContextType.active_state.value == "active_state"
        assert ContextType.custom.value == "custom"

    def test_context_block_defaults(self):
        b = ContextBlock(id="abc123", type=ContextType.architecture, title="Arch", content="Details")
        assert b.tags == []
        assert b.source is None
        assert b.version == 1
        assert b.created_at == ""
        assert b.updated_at == ""

    def test_context_block_full(self):
        b = ContextBlock(
            id="abc",
            type=ContextType.coding_standards,
            title="Standards",
            content="Use 4 spaces",
            tags=["python", "style"],
            source="CONTRIBUTING.md",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            version=3,
        )
        assert b.tags == ["python", "style"]
        assert b.source == "CONTRIBUTING.md"
        assert b.version == 3

    def test_register_request(self):
        r = RegisterContextRequest(
            type=ContextType.architecture,
            title="System Design",
            content="Microservices architecture",
            tags=["backend"],
            source="ADR-001.md",
        )
        assert r.type == ContextType.architecture
        assert r.title == "System Design"
        assert r.source == "ADR-001.md"

    def test_register_request_defaults(self):
        r = RegisterContextRequest(
            type=ContextType.roadmap,
            title="Q1 Goals",
            content="Ship v2.0",
        )
        assert r.tags == []
        assert r.source is None

    def test_register_response(self):
        r = RegisterContextResponse(id="abc", type=ContextType.custom, title="My Block", version=1)
        assert r.message == "Context block registered"
        assert r.id == "abc"

    def test_update_request(self):
        r = UpdateContextRequest(id="abc", content="New content")
        assert r.id == "abc"
        assert r.content == "New content"
        assert r.title is None
        assert r.type is None

    def test_update_response(self):
        r = UpdateContextResponse(id="abc", version=2)
        assert r.message == "Context block updated"

    def test_search_request_defaults(self):
        r = SearchContextRequest(query="microservices")
        assert r.top_k == 10
        assert r.type_filter is None

    def test_search_result_defaults(self):
        r = ContextSearchResult(
            id="abc",
            type=ContextType.architecture,
            title="Arch",
            content="Details",
            tags=[],
            version=1,
        )
        assert r.score == 0.0

    def test_search_response(self):
        results = [
            ContextSearchResult(id="a", type=ContextType.architecture, title="A", content="", tags=[], version=1),
            ContextSearchResult(id="b", type=ContextType.architecture, title="B", content="", tags=[], version=1),
        ]
        r = SearchContextResponse(results=results, query="arch", total_hits=2)
        assert len(r.results) == 2
        assert r.total_hits == 2

    def test_context_response(self):
        r = ContextResponse(
            id="abc",
            type=ContextType.active_state,
            title="Current Sprint",
            content="Sprint 12",
            tags=["agile"],
            source=None,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            version=1,
        )
        assert r.type == ContextType.active_state
        assert r.content == "Sprint 12"


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def store(redis):
    s = ContextStore(redis_client=redis)
    yield s


class TestContextStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self, store):
        block = ContextBlock(
            id="test1",
            type=ContextType.architecture,
            title="Architecture",
            content="System architecture description",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert await store.save(block) is True
        retrieved = await store.get("test1")
        assert retrieved is not None
        assert retrieved.id == "test1"
        assert retrieved.title == "Architecture"
        assert retrieved.content == "System architecture description"

    @pytest.mark.asyncio
    async def test_get_missing(self, store):
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_save_adds_to_ids_set(self, store):
        block = ContextBlock(id="a1", type=ContextType.roadmap, title="Roadmap", content="Plan", created_at="", updated_at="")
        await store.save(block)
        ids = await store.list_ids()
        assert "a1" in ids

    @pytest.mark.asyncio
    async def test_save_adds_to_type_set(self, store):
        block = ContextBlock(id="b1", type=ContextType.coding_standards, title="Standards", content="Rules", created_at="", updated_at="")
        await store.save(block)
        ids = await store.list_ids(type_filter=ContextType.coding_standards)
        assert "b1" in ids

    @pytest.mark.asyncio
    async def test_update_content(self, store):
        block = ContextBlock(id="u1", type=ContextType.architecture, title="Arch", content="Old", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
        await store.save(block)
        updated = await store.update("u1", content="New content")
        assert updated is not None
        assert updated.content == "New content"
        assert updated.version == 2
        assert updated.updated_at != ""

    @pytest.mark.asyncio
    async def test_update_title(self, store):
        block = ContextBlock(id="u2", type=ContextType.roadmap, title="Old Title", content="Content", created_at="", updated_at="")
        await store.save(block)
        updated = await store.update("u2", title="New Title")
        assert updated.title == "New Title"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_update_type_removes_old_type_set(self, store):
        block = ContextBlock(id="u3", type=ContextType.architecture, title="Arch", content="Content", created_at="", updated_at="")
        await store.save(block)
        await store.update("u3", type=ContextType.roadmap)
        arch_ids = await store.list_ids(type_filter=ContextType.architecture)
        roadmap_ids = await store.list_ids(type_filter=ContextType.roadmap)
        assert "u3" not in arch_ids
        assert "u3" in roadmap_ids

    @pytest.mark.asyncio
    async def test_update_missing_returns_none(self, store):
        result = await store.update("nonexistent", content="Anything")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, store):
        block = ContextBlock(id="d1", type=ContextType.architecture, title="Arch", content="Content", created_at="", updated_at="")
        await store.save(block)
        assert await store.delete("d1") is True
        assert await store.get("d1") is None
        ids = await store.list_ids()
        assert "d1" not in ids

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self, store):
        assert await store.delete("nonexistent") is False

    @pytest.mark.asyncio
    async def test_count(self, store):
        for i in range(5):
            block = ContextBlock(id=f"c{i}", type=ContextType.custom, title=f"Block {i}", content="Content", created_at="", updated_at="")
            await store.save(block)
        assert await store.count() == 5

    @pytest.mark.asyncio
    async def test_count_by_type(self, store):
        for i in range(3):
            block = ContextBlock(id=f"t{i}", type=ContextType.architecture, title=f"Arch {i}", content="Content", created_at="", updated_at="")
            await store.save(block)
        block2 = ContextBlock(id="t_road", type=ContextType.roadmap, title="Road", content="Content", created_at="", updated_at="")
        await store.save(block2)
        assert await store.count(type_filter=ContextType.architecture) == 3
        assert await store.count(type_filter=ContextType.roadmap) == 1

    @pytest.mark.asyncio
    async def test_clear_all(self, store):
        for i in range(3):
            block = ContextBlock(id=f"cl{i}", type=ContextType.architecture, title=f"Block {i}", content="Content", created_at="", updated_at="")
            await store.save(block)
        await store.clear_all()
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_no_redis_no_op(self):
        s = ContextStore(redis_client=None)
        assert await s.save(ContextBlock(id="x", type=ContextType.custom, title="X", content="X", created_at="", updated_at="")) is False
        assert await s.get("x") is None
        assert await s.search("test") == []
        assert await s.count() == 0
        assert await s.list_ids() == []

    @pytest.mark.asyncio
    async def test_register_block_helper(self, store):
        block = await register_block(
            store=store,
            type_=ContextType.architecture,
            title="Test Arch",
            content="Architectural overview",
            tags=["system", "design"],
            source="README.md",
        )
        assert block.id is not None
        assert len(block.id) == 16
        assert block.type == ContextType.architecture
        assert block.title == "Test Arch"
        assert block.tags == ["system", "design"]
        assert block.source == "README.md"
        assert block.version == 1
        assert block.created_at != ""
        assert block.updated_at == block.created_at
        retrieved = await store.get(block.id)
        assert retrieved is not None
        assert retrieved.title == "Test Arch"


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestContextSearch:
    @pytest.fixture
    async def populated_store(self):
        r = FakeAsyncRedis(decode_responses=True)
        s = ContextStore(redis_client=r)
        blocks_data = [
            ("s1", ContextType.architecture, "System Architecture", "Microservices with event-driven communication", ["backend", "design"]),
            ("s2", ContextType.coding_standards, "Python Style Guide", "Use 4 spaces, snake_case, type hints", ["python", "style", "linting"]),
            ("s3", ContextType.roadmap, "Q1 2026 Roadmap", "Ship v2.0 with AI features", ["planning", "goals"]),
            ("s4", ContextType.active_state, "Current Sprint", "Working on context service integration", ["sprint", "progress"]),
            ("s5", ContextType.architecture, "Database Architecture", "PostgreSQL with Redis caching layer", ["backend", "database"]),
        ]
        for id_, typ, title, content, tags in blocks_data:
            await register_block(store=s, type_=typ, title=title, content=content, tags=tags)
        yield s
        await r.aclose()

    @pytest.mark.asyncio
    async def test_search_by_title_match(self, populated_store):
        results = await populated_store.search("System Architecture")
        assert len(results) >= 1
        assert results[0].title == "System Architecture"

    @pytest.mark.asyncio
    async def test_search_by_content(self, populated_store):
        results = await populated_store.search("Microservices")
        assert len(results) >= 1
        assert any("Microservices" in r.content for r in results)

    @pytest.mark.asyncio
    async def test_search_by_tag(self, populated_store):
        results = await populated_store.search("python")
        assert len(results) >= 1
        assert results[0].type == ContextType.coding_standards

    @pytest.mark.asyncio
    async def test_search_with_type_filter(self, populated_store):
        results = await populated_store.search("architecture", type_filter=ContextType.architecture)
        assert len(results) >= 1
        for r in results:
            assert r.type == ContextType.architecture

    @pytest.mark.asyncio
    async def test_search_excludes_other_types(self, populated_store):
        results = await populated_store.search("architecture", type_filter=ContextType.coding_standards)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_results_scored(self, populated_store):
        results = await populated_store.search("architecture")
        assert len(results) >= 1
        for r in results:
            assert r.score > 0

    @pytest.mark.asyncio
    async def test_search_top_k_limits(self, populated_store):
        results = await populated_store.search("architecture", top_k=1)
        assert len(results) <= 1

    @pytest.mark.asyncio
    async def test_search_empty_query(self, populated_store):
        results = await populated_store.search("")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_no_match(self, populated_store):
        results = await populated_store.search("zzz_nonexistent_zzz")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_result_fields(self, populated_store):
        results = await populated_store.search("Python")
        assert len(results) >= 1
        r = results[0]
        assert r.id is not None
        assert r.type is not None
        assert r.title is not None
        assert r.content is not None
        assert isinstance(r.tags, list)
        assert isinstance(r.version, int)
        assert isinstance(r.score, float)
