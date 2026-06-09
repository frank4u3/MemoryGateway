import json
from datetime import datetime

import pytest
from fakeredis import FakeAsyncRedis

from gateway.learning.schemas import (
    Learning,
    LearningResponse,
    LearningSearchResult,
    LearningType,
    SearchLearningRequest,
    SearchLearningResponse,
    StoreLearningRequest,
    StoreLearningResponse,
    UpdateLearningRequest,
    UpdateLearningResponse,
)
from gateway.learning.store import LearningStore


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def store(redis):
    s = LearningStore(redis_client=redis)
    yield s


@pytest.fixture
async def populated_store(store):
    learnings_data = [
        ("l1", LearningType.bug_fix, "Fix: Database timeout",
         "Increased connection pool size from 10 to 50 to resolve p99 latency spikes under load.",
         "issue-142", "hermes", ["db", "performance", "postgres"], "webapp"),
        ("l2", LearningType.arch_decision, "ADR: Redis for session storage",
         "Chose Redis over memcached for session persistence due to built-in replication and persistence.",
         "issue-201", "opencode", ["redis", "sessions", "architecture"], "webapp"),
        ("l3", LearningType.migration_procedure, "Migrate: Sqlalchemy 1.4 -> 2.0",
         "Step-by-step migration guide including the async engine switch and removed autocommit patterns.",
         "", "qoder", ["sqlalchemy", "migration", "python"], "backend"),
        ("l4", LearningType.deployment_fix, "Fix: Missing env var in staging",
         "Added DATABASE_URL to the staging k8s secret after deployments to staging kept failing with connection errors.",
         "incident-78", "hermes", ["k8s", "deployment", "env"], "infra"),
        ("l5", LearningType.bug_fix, "Fix: Memory leak in worker",
         "Worker was not closing HTTP connections after each job. Added explicit session.close() in finally block.",
         "issue-305", "vscode", ["memory-leak", "python", "http"], "workers"),
    ]
    for id_, typ, title, content, source, resolved, tags, project in learnings_data:
        ln = Learning(
            id=id_,
            type=typ,
            title=title,
            content=content,
            source_issue=source,
            resolved_by=resolved,
            tags=tags,
            project=project,
            created_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            updated_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            version=1,
        )
        await store.save(ln)
    yield store


class TestLearningSchemas:
    def test_learning_type_enum(self):
        assert LearningType.bug_fix == "bug_fix"
        assert LearningType.arch_decision == "arch_decision"
        assert LearningType.migration_procedure == "migration_procedure"
        assert LearningType.deployment_fix == "deployment_fix"

    def test_learning_defaults(self):
        ln = Learning(id="abc", type=LearningType.bug_fix, title="Test", content="Content")
        assert ln.id == "abc"
        assert ln.source_issue == ""
        assert ln.resolved_by == ""
        assert ln.tags == []
        assert ln.project is None
        assert ln.created_at == ""
        assert ln.updated_at == ""
        assert ln.version == 1

    def test_store_request_defaults(self):
        req = StoreLearningRequest(type=LearningType.bug_fix, title="T", content="C")
        assert req.source_issue == ""
        assert req.resolved_by == ""
        assert req.tags == []
        assert req.project is None

    def test_store_response(self):
        resp = StoreLearningResponse(id="abc", type=LearningType.bug_fix, title="T", version=2)
        assert resp.message == "Learning stored"

    def test_update_request(self):
        req = UpdateLearningRequest(id="abc", title="New Title")
        assert req.id == "abc"
        assert req.title == "New Title"
        assert req.type is None
        assert req.content is None

    def test_update_response(self):
        resp = UpdateLearningResponse(id="abc", version=3)
        assert resp.message == "Learning updated"

    def test_search_request(self):
        req = SearchLearningRequest(query="database")
        assert req.query == "database"
        assert req.type_filter is None
        assert req.top_k == 10
        assert req.use_semantic is True

    def test_search_result_default_score(self):
        sr = LearningSearchResult(
            id="x", type=LearningType.bug_fix, title="T", content="C",
            source_issue="", resolved_by="", tags=[], version=1,
        )
        assert sr.score == 0.0

    def test_search_response(self):
        resp = SearchLearningResponse(results=[], query="q", total_hits=0)
        assert resp.results == []
        assert resp.total_hits == 0

    def test_learning_response(self):
        resp = LearningResponse(
            id="x", type=LearningType.bug_fix, title="T", content="C",
            source_issue="", resolved_by="", tags=[], version=1,
            created_at="", updated_at="",
        )
        assert resp.id == "x"


class TestLearningStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self, store):
        ln = Learning(id="l1", type=LearningType.bug_fix, title="Fix bug", content="Fixed a race condition")
        saved = await store.save(ln)
        assert saved is True

        fetched = await store.get("l1")
        assert fetched is not None
        assert fetched.title == "Fix bug"

    @pytest.mark.asyncio
    async def test_get_missing(self, store):
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_save_adds_to_ids_set(self, store):
        ln = Learning(id="l1", type=LearningType.bug_fix, title="T", content="C")
        await store.save(ln)

        ids = await store.list_ids()
        assert "l1" in ids

    @pytest.mark.asyncio
    async def test_save_adds_to_type_set(self, store):
        ln = Learning(id="l1", type=LearningType.bug_fix, title="T", content="C")
        await store.save(ln)

        ids = await store.list_ids(type_filter=LearningType.bug_fix)
        assert "l1" in ids

        other_ids = await store.list_ids(type_filter=LearningType.arch_decision)
        assert "l1" not in other_ids

    @pytest.mark.asyncio
    async def test_update_content(self, store):
        ln = Learning(id="l1", type=LearningType.bug_fix, title="Original", content="Original")
        await store.save(ln)

        updated = await store.update("l1", title="Updated", content="New content")
        assert updated is not None
        assert updated.title == "Updated"
        assert updated.content == "New content"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_update_type_migration(self, store):
        ln = Learning(id="l1", type=LearningType.bug_fix, title="T", content="C")
        await store.save(ln)

        updated = await store.update("l1", type=LearningType.arch_decision)
        assert updated is not None
        assert updated.type == LearningType.arch_decision

        bug_ids = await store.list_ids(type_filter=LearningType.bug_fix)
        assert "l1" not in bug_ids

        arch_ids = await store.list_ids(type_filter=LearningType.arch_decision)
        assert "l1" in arch_ids

    @pytest.mark.asyncio
    async def test_update_missing(self, store):
        assert await store.update("nonexistent", title="X") is None

    @pytest.mark.asyncio
    async def test_delete(self, store):
        ln = Learning(id="l1", type=LearningType.bug_fix, title="T", content="C")
        await store.save(ln)

        assert await store.delete("l1") is True
        assert await store.get("l1") is None
        ids = await store.list_ids()
        assert "l1" not in ids

    @pytest.mark.asyncio
    async def test_delete_missing(self, store):
        assert await store.delete("nonexistent") is False

    @pytest.mark.asyncio
    async def test_count(self, store):
        assert await store.count() == 0

        await store.save(Learning(id="l1", type=LearningType.bug_fix, title="T", content="C"))
        assert await store.count() == 1
        assert await store.count(type_filter=LearningType.bug_fix) == 1
        assert await store.count(type_filter=LearningType.arch_decision) == 0

    @pytest.mark.asyncio
    async def test_clear_all(self, store):
        await store.save(Learning(id="l1", type=LearningType.bug_fix, title="T", content="C"))
        await store.save(Learning(id="l2", type=LearningType.arch_decision, title="T", content="C"))
        assert await store.count() == 2

        await store.clear_all()
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_no_redis_no_op(self):
        s = LearningStore(redis_client=None)
        assert await s.save(Learning(id="x", type=LearningType.bug_fix, title="T", content="C")) is False
        assert await s.get("x") is None
        assert await s.update("x", title="Y") is None
        assert await s.delete("x") is False
        assert await s.search("q") == []
        assert await s.count() == 0
        assert await s.list_ids() == []


class TestLearningSearch:
    @pytest.mark.asyncio
    async def test_search_by_title_exact(self, populated_store):
        results = await populated_store.search("Fix: Database timeout", use_semantic=False)
        assert len(results) > 0
        assert results[0].title == "Fix: Database timeout"

    @pytest.mark.asyncio
    async def test_search_by_content(self, populated_store):
        results = await populated_store.search("connection pool", use_semantic=False)
        assert len(results) > 0
        assert any("pool" in r.content.lower() for r in results)

    @pytest.mark.asyncio
    async def test_search_by_tag(self, populated_store):
        results = await populated_store.search("redis", use_semantic=False)
        assert len(results) > 0
        assert any("redis" in r.tags for r in results)

    @pytest.mark.asyncio
    async def test_search_by_resolved_by(self, populated_store):
        results = await populated_store.search("hermes", use_semantic=False)
        assert len(results) > 0
        assert all(r.resolved_by == "hermes" or "hermes" in r.title.lower() or "hermes" in r.content.lower() for r in results)

    @pytest.mark.asyncio
    async def test_search_with_type_filter(self, populated_store):
        results = await populated_store.search(
            "fix", type_filter=LearningType.deployment_fix, use_semantic=False
        )
        assert len(results) > 0
        for r in results:
            assert r.type == LearningType.deployment_fix

    @pytest.mark.asyncio
    async def test_search_excludes_other_types(self, populated_store):
        results = await populated_store.search(
            "Fix", type_filter=LearningType.arch_decision, use_semantic=False
        )
        for r in results:
            assert r.type == LearningType.arch_decision

    @pytest.mark.asyncio
    async def test_search_top_k_limits(self, populated_store):
        results = await populated_store.search("fix", top_k=2, use_semantic=False)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_search_empty_query(self, populated_store):
        results = await populated_store.search("", use_semantic=False)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_no_match(self, populated_store):
        results = await populated_store.search("zzzzinvalidqueryzzzz", use_semantic=False)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_results_scored(self, populated_store):
        results = await populated_store.search("database", use_semantic=False)
        assert len(results) > 0
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_search_result_fields(self, populated_store):
        results = await populated_store.search("migration", use_semantic=False)
        assert len(results) > 0
        r = results[0]
        assert r.id
        assert r.type in LearningType
        assert r.title
        assert r.content
        assert isinstance(r.tags, list)
        assert r.version >= 1
        assert r.score > 0

    @pytest.mark.asyncio
    async def test_search_by_project(self, populated_store):
        results = await populated_store.search("webapp", use_semantic=False)
        assert len(results) > 0
        assert any(r.project == "webapp" for r in results)

    @pytest.mark.asyncio
    async def test_search_multi_word(self, populated_store):
        results = await populated_store.search("memory leak python", use_semantic=False)
        assert len(results) > 0
        assert any("memory" in r.title.lower() or "leak" in r.title.lower() for r in results)

    @pytest.mark.asyncio
    async def test_search_no_redis(self):
        s = LearningStore(redis_client=None)
        results = await s.search("anything")
        assert results == []
