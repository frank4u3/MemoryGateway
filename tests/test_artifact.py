import pytest
from fakeredis import FakeAsyncRedis

from gateway.artifact.schemas import (
    Artifact,
    ArtifactResponse,
    ArtifactSearchResult,
    ArtifactType,
    SearchArtifactRequest,
    SearchArtifactResponse,
    StoreArtifactRequest,
    StoreArtifactResponse,
    UpdateArtifactRequest,
    UpdateArtifactResponse,
)
from gateway.artifact.store import ArtifactStore


class TestArtifactSchemas:
    def test_artifact_type_enum(self):
        assert ArtifactType.generated_code.value == "generated_code"
        assert ArtifactType.api.value == "api"
        assert ArtifactType.prompt.value == "prompt"
        assert ArtifactType.workflow.value == "workflow"
        assert ArtifactType.schema.value == "schema"
        assert ArtifactType.architecture_decision.value == "architecture_decision"

    def test_artifact_defaults(self):
        a = Artifact(
            id="abc123",
            type=ArtifactType.generated_code,
            title="Auth Middleware",
            content="def auth(): pass",
            creator_agent="hermes",
        )
        assert a.tags == []
        assert a.git_commit is None
        assert a.project is None
        assert a.version == 1
        assert a.created_at == ""
        assert a.updated_at == ""

    def test_artifact_full(self):
        a = Artifact(
            id="abc",
            type=ArtifactType.api,
            title="User API",
            content="GET /users",
            creator_agent="opencode",
            git_commit="a1b2c3d",
            tags=["rest", "users"],
            project="memory-gateway",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            version=3,
        )
        assert a.git_commit == "a1b2c3d"
        assert a.project == "memory-gateway"
        assert a.version == 3

    def test_store_request(self):
        r = StoreArtifactRequest(
            type=ArtifactType.prompt,
            title="Code Review",
            content="Review this code",
            creator_agent="qoder",
            tags=["review"],
            project="backend",
        )
        assert r.type == ArtifactType.prompt
        assert r.creator_agent == "qoder"
        assert r.project == "backend"

    def test_store_request_defaults(self):
        r = StoreArtifactRequest(
            type=ArtifactType.workflow,
            title="Deploy",
            content="Deploy steps",
            creator_agent="hermes",
        )
        assert r.tags == []
        assert r.git_commit is None
        assert r.project is None

    def test_store_response(self):
        r = StoreArtifactResponse(id="abc", type=ArtifactType.schema, title="User Schema", version=1)
        assert r.message == "Artifact stored"

    def test_update_request(self):
        r = UpdateArtifactRequest(id="abc", content="New content", git_commit="def456")
        assert r.content == "New content"
        assert r.git_commit == "def456"
        assert r.title is None

    def test_update_response(self):
        r = UpdateArtifactResponse(id="abc", version=2)
        assert r.message == "Artifact updated"

    def test_search_request_defaults(self):
        r = SearchArtifactRequest(query="auth")
        assert r.top_k == 10
        assert r.type_filter is None
        assert r.use_semantic is True

    def test_search_result_defaults(self):
        r = ArtifactSearchResult(
            id="abc",
            type=ArtifactType.generated_code,
            title="Auth",
            content="code",
            creator_agent="hermes",
            tags=[],
            version=1,
        )
        assert r.score == 0.0

    def test_search_response(self):
        results = [
            ArtifactSearchResult(id="a", type=ArtifactType.generated_code, title="A", content="", creator_agent="hermes", tags=[], version=1),
            ArtifactSearchResult(id="b", type=ArtifactType.api, title="B", content="", creator_agent="opencode", tags=[], version=1),
        ]
        r = SearchArtifactResponse(results=results, query="api", total_hits=2)
        assert len(r.results) == 2
        assert r.total_hits == 2

    def test_artifact_response(self):
        r = ArtifactResponse(
            id="abc",
            type=ArtifactType.architecture_decision,
            title="Use Redis",
            content="Chose Redis for caching",
            creator_agent="hermes",
            git_commit="abc123",
            tags=["cache"],
            project="gateway",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            version=1,
        )
        assert r.type == ArtifactType.architecture_decision
        assert r.project == "gateway"


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def store(redis):
    s = ArtifactStore(redis_client=redis)
    yield s


class TestArtifactStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self, store):
        a = Artifact(
            id="test1",
            type=ArtifactType.generated_code,
            title="Auth Middleware",
            content="def auth(): pass",
            creator_agent="hermes",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert await store.save(a) is True
        retrieved = await store.get("test1")
        assert retrieved is not None
        assert retrieved.id == "test1"
        assert retrieved.title == "Auth Middleware"
        assert retrieved.creator_agent == "hermes"

    @pytest.mark.asyncio
    async def test_get_missing(self, store):
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_save_adds_to_ids_set(self, store):
        a = Artifact(id="a1", type=ArtifactType.api, title="API", content="GET /api", creator_agent="opencode", created_at="", updated_at="")
        await store.save(a)
        ids = await store.list_ids()
        assert "a1" in ids

    @pytest.mark.asyncio
    async def test_save_adds_to_type_set(self, store):
        a = Artifact(id="b1", type=ArtifactType.prompt, title="Review", content="Review prompt", creator_agent="qoder", created_at="", updated_at="")
        await store.save(a)
        ids = await store.list_ids(type_filter=ArtifactType.prompt)
        assert "b1" in ids

    @pytest.mark.asyncio
    async def test_update_content(self, store):
        a = Artifact(id="u1", type=ArtifactType.generated_code, title="Module", content="Old code", creator_agent="hermes", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
        await store.save(a)
        updated = await store.update("u1", content="New code")
        assert updated is not None
        assert updated.content == "New code"
        assert updated.version == 2
        assert updated.updated_at != ""

    @pytest.mark.asyncio
    async def test_update_title(self, store):
        a = Artifact(id="u2", type=ArtifactType.workflow, title="Old Title", content="Steps", creator_agent="hermes", created_at="", updated_at="")
        await store.save(a)
        updated = await store.update("u2", title="New Title")
        assert updated.title == "New Title"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_update_git_commit(self, store):
        a = Artifact(id="u3", type=ArtifactType.schema, title="Schema", content="CREATE TABLE", creator_agent="opencode", created_at="", updated_at="")
        await store.save(a)
        updated = await store.update("u3", git_commit="newhash")
        assert updated.git_commit == "newhash"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_update_project(self, store):
        a = Artifact(id="u4", type=ArtifactType.architecture_decision, title="ADR", content="Decision", creator_agent="hermes", created_at="", updated_at="")
        await store.save(a)
        updated = await store.update("u4", project="new-project")
        assert updated.project == "new-project"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_update_type_removes_old_type_set(self, store):
        a = Artifact(id="u5", type=ArtifactType.generated_code, title="Module", content="Code", creator_agent="hermes", created_at="", updated_at="")
        await store.save(a)
        await store.update("u5", type=ArtifactType.api)
        code_ids = await store.list_ids(type_filter=ArtifactType.generated_code)
        api_ids = await store.list_ids(type_filter=ArtifactType.api)
        assert "u5" not in code_ids
        assert "u5" in api_ids

    @pytest.mark.asyncio
    async def test_update_missing_returns_none(self, store):
        result = await store.update("nonexistent", content="Anything")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, store):
        a = Artifact(id="d1", type=ArtifactType.api, title="API", content="DELETE /api", creator_agent="opencode", created_at="", updated_at="")
        await store.save(a)
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
            a = Artifact(id=f"c{i}", type=ArtifactType.generated_code, title=f"Artifact {i}", content="Content", creator_agent="hermes", created_at="", updated_at="")
            await store.save(a)
        assert await store.count() == 5

    @pytest.mark.asyncio
    async def test_count_by_type(self, store):
        for i in range(3):
            a = Artifact(id=f"t{i}", type=ArtifactType.api, title=f"API {i}", content="Content", creator_agent="hermes", created_at="", updated_at="")
            await store.save(a)
        a2 = Artifact(id="t_work", type=ArtifactType.workflow, title="Workflow", content="Steps", creator_agent="opencode", created_at="", updated_at="")
        await store.save(a2)
        assert await store.count(type_filter=ArtifactType.api) == 3
        assert await store.count(type_filter=ArtifactType.workflow) == 1

    @pytest.mark.asyncio
    async def test_clear_all(self, store):
        for i in range(3):
            a = Artifact(id=f"cl{i}", type=ArtifactType.generated_code, title=f"Artifact {i}", content="Content", creator_agent="hermes", created_at="", updated_at="")
            await store.save(a)
        await store.clear_all()
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_no_redis_no_op(self):
        s = ArtifactStore(redis_client=None)
        a = Artifact(id="x", type=ArtifactType.generated_code, title="X", content="X", creator_agent="hermes", created_at="", updated_at="")
        assert await s.save(a) is False
        assert await s.get("x") is None
        assert await s.search("test") == []
        assert await s.count() == 0
        assert await s.list_ids() == []


class TestArtifactSearch:
    @pytest.fixture
    async def populated_store(self):
        r = FakeAsyncRedis(decode_responses=True)
        s = ArtifactStore(redis_client=r)
        artifacts_data = [
            ("a1", ArtifactType.generated_code, "Auth Middleware", "JWT authentication middleware for FastAPI", "hermes", ["auth", "security"], "memory-gateway"),
            ("a2", ArtifactType.api, "User API", "RESTful user management API with CRUD operations", "opencode", ["rest", "users"], "memory-gateway"),
            ("a3", ArtifactType.prompt, "Code Review Prompt", "Systematic code review prompt template", "qoder", ["review", "template"], "opencode"),
            ("a4", ArtifactType.workflow, "CI Pipeline", "GitHub Actions workflow for testing and deployment", "vscode", ["ci", "devops"], "memory-gateway"),
            ("a5", ArtifactType.architecture_decision, "Use Redis for Caching", "Decision to use Redis as primary cache layer", "hermes", ["cache", "architecture"], "memory-gateway"),
        ]
        for id_, typ, title, content, creator, tags, project in artifacts_data:
            a = Artifact(
                id=id_, type=typ, title=title, content=content,
                creator_agent=creator, tags=tags, project=project,
                created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
            )
            await s.save(a)
        yield s
        await r.aclose()

    @pytest.mark.asyncio
    async def test_search_by_title_match(self, populated_store):
        results = await populated_store.search("Auth Middleware")
        assert len(results) >= 1
        assert results[0].title == "Auth Middleware"

    @pytest.mark.asyncio
    async def test_search_by_content(self, populated_store):
        results = await populated_store.search("JWT authentication")
        assert len(results) >= 1
        assert any("JWT" in r.content for r in results)

    @pytest.mark.asyncio
    async def test_search_by_tag(self, populated_store):
        results = await populated_store.search("security")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_by_creator_agent(self, populated_store):
        results = await populated_store.search("hermes")
        assert len(results) >= 1
        for r in results:
            assert r.creator_agent == "hermes"

    @pytest.mark.asyncio
    async def test_search_by_project(self, populated_store):
        results = await populated_store.search("opencode")
        assert len(results) >= 1
        assert any(r.project == "opencode" for r in results)

    @pytest.mark.asyncio
    async def test_search_with_type_filter(self, populated_store):
        results = await populated_store.search("api", type_filter=ArtifactType.api)
        assert len(results) >= 1
        for r in results:
            assert r.type == ArtifactType.api

    @pytest.mark.asyncio
    async def test_search_excludes_other_types(self, populated_store):
        results = await populated_store.search("api", type_filter=ArtifactType.prompt)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_results_scored(self, populated_store):
        results = await populated_store.search("cache")
        assert len(results) >= 1
        for r in results:
            assert r.score > 0

    @pytest.mark.asyncio
    async def test_search_top_k_limits(self, populated_store):
        results = await populated_store.search("api", top_k=1)
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
    async def test_search_turns_off_semantic(self, populated_store):
        results = await populated_store.search("api", use_semantic=False)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_result_fields(self, populated_store):
        results = await populated_store.search("Redis")
        assert len(results) >= 1
        r = results[0]
        assert r.id is not None
        assert r.type is not None
        assert r.title is not None
        assert r.content is not None
        assert r.creator_agent is not None
        assert isinstance(r.tags, list)
        assert isinstance(r.version, int)
        assert isinstance(r.score, float)
