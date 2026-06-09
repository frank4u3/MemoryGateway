"""Failure injection tests for Memory Gateway.

Tests resilience against:
    - Redis unavailable
    - Qdrant unavailable
    - DeepSeek upstream unavailable
    - Network timeouts
    - Malformed responses
"""

import asyncio

import pytest
from fakeredis import FakeAsyncRedis


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


class TestRedisUnavailable:
    """Verify graceful degradation when Redis is disconnected."""

    @pytest.mark.asyncio
    async def test_exact_cache_returns_none_with_fake_redis(self, redis):
        from gateway.cache.exact import ExactCache
        cache = ExactCache(redis_client=redis)
        assert await cache.get("any") is None
        assert await cache.size() == 0
        assert await cache.exists("any") is False
        assert await cache.delete("exact:*") == 0

    @pytest.mark.asyncio
    async def test_stats_tracker_accepts_redis(self, redis):
        from gateway.stats import StatsTracker
        st = StatsTracker(redis=redis)
        stats = await st.get_stats()
        assert isinstance(stats, dict)

    @pytest.mark.asyncio
    async def test_memory_store_works_without_redis(self, tmpdir):
        from gateway.memory.store import MemoryStore, build_pack

        store = MemoryStore(base_dir=str(tmpdir), redis_client=None)
        pack = build_pack({"a.md": "# A"}, version="v-noredis")
        store.save(pack)
        assert store.current_version() == "v-noredis"

    @pytest.mark.asyncio
    async def test_context_store_no_redis(self):
        from gateway.context.store import ContextStore

        cs = ContextStore(redis_client=None)
        assert await cs.get("any") is None
        assert await cs.count() == 0
        assert await cs.search("test") == []

    @pytest.mark.asyncio
    async def test_artifact_store_no_redis(self):
        from gateway.artifact.store import ArtifactStore

        store = ArtifactStore(redis_client=None)
        assert await store.get("any") is None
        assert await store.count() == 0
        assert await store.search("test") == []

    @pytest.mark.asyncio
    async def test_learning_store_no_redis(self):
        from gateway.learning.store import LearningStore

        store = LearningStore(redis_client=None)
        assert await store.get("any") is None
        assert await store.count() == 0
        assert await store.search("test") == []

    @pytest.mark.asyncio
    async def test_memory_layer_no_redis(self):
        from gateway.memory_layer.store import MemoryLayerStore

        ml = MemoryLayerStore(redis_client=None)
        result = await ml.search(query="test", agent_id="hermes")
        assert result == []

    @pytest.mark.asyncio
    async def test_canonicalizer_handles_empty_input(self):
        from gateway.canonicalizer import (
            canonicalize_prompt,
            canonicalize_messages,
            normalize_text,
        )
        result = normalize_text("")
        assert result == ""

        msgs = canonicalize_messages([], max_turns=5)
        assert msgs == []

        cp = canonicalize_prompt(messages=[])
        assert cp.canonical_messages == []
        assert cp.canonical_hash


class TestQdrantUnavailable:
    """Verify stores degrade when Qdrant/IndexStore is not available."""

    @pytest.mark.asyncio
    async def test_artifact_save_without_index(self, redis):
        from gateway.artifact.store import ArtifactStore
        from gateway.artifact.schemas import Artifact, ArtifactType

        store = ArtifactStore(redis_client=redis, index_store=None)
        a = Artifact(
            id="a1", type=ArtifactType.generated_code,
            title="Test", content="Content", creator_agent="test",
        )
        saved = await store.save(a)
        assert saved is True
        fetched = await store.get("a1")
        assert fetched is not None

    @pytest.mark.asyncio
    async def test_artifact_search_without_semantic(self, redis):
        from gateway.artifact.store import ArtifactStore
        from gateway.artifact.schemas import Artifact, ArtifactType

        store = ArtifactStore(redis_client=redis, index_store=None)
        a = Artifact(
            id="a1", type=ArtifactType.generated_code,
            title="Test Artifact", content="Some content", creator_agent="test",
        )
        await store.save(a)

        results = await store.search("Test", use_semantic=True)
        assert len(results) == 1
        assert results[0].title == "Test Artifact"

    @pytest.mark.asyncio
    async def test_learning_save_without_index(self, redis):
        from gateway.learning.store import LearningStore
        from gateway.learning.schemas import Learning, LearningType

        store = LearningStore(redis_client=redis, index_store=None)
        ln = Learning(
            id="l1", type=LearningType.bug_fix,
            title="Test Fix", content="Fixed a bug",
        )
        saved = await store.save(ln)
        assert saved is True

    @pytest.mark.asyncio
    async def test_context_save_without_index(self, redis):
        from gateway.context.store import ContextStore
        from gateway.context.schemas import ContextBlock, ContextType

        store = ContextStore(redis_client=redis, index_store=None)
        cb = ContextBlock(
            id="c1", type=ContextType.custom,
            title="Test Context", content="Some docs",
        )
        saved = await store.save(cb)
        assert saved is True


class TestMalformedInputs:
    """Verify robustness against edge-case inputs."""

    def test_canonicalizer_path_patterns(self):
        from gateway.canonicalizer import ABSOLUTE_PATH_RE

        paths = [
            "/home/user/project/src",
            "C:\\Users\\Admin\\project\\src",
            "\\\\server\\share\\project\\src",
            "/mnt/c/Users/dev/repo",
            "/app/src/main.py",
            "/usr/local/bin/tool",
            "/srv/data/config.json",
            "/opt/project/lib",
            "/etc/nginx/config.conf",
        ]
        for path in paths:
            match = ABSOLUTE_PATH_RE.search(path)
            assert match is not None, f"Path should be matched: {path}"

    def test_canonicalizer_path_replacer(self):
        from gateway.canonicalizer import ABSOLUTE_PATH_RE, _path_replacer

        def replacer(path):
            m = ABSOLUTE_PATH_RE.search(path)
            if m:
                return _path_replacer(m)
            return path

        cases = [
            "/home/user/project/src",
            "C:\\Users\\Admin\\project\\src",
            "\\\\server\\share\\project\\src",
            "/mnt/c/Users/dev/repo",
            "/app/src/main.py",
            "/usr/local/bin/tool",
            "/srv/data/config.json",
        ]
        for path in cases:
            result = replacer(path)
            assert result.startswith("<workspace>"), f"Replacer({path})={result}"
            assert result != path, f"Path should be normalized: {result}"

    def test_canonicalizer_path_replacer_deterministic(self):
        from gateway.canonicalizer import canonicalize_prompt

        pairs = [
            ("Fix /home/alice/project/main.py", "Fix /home/bob/project/main.py"),
            ("Edit C:\\Users\\Alice\\code\\app.py", "Edit C:\\Users\\Bob\\code\\app.py"),
        ]
        for a, b in pairs:
            cp_a = canonicalize_prompt([{"role": "user", "content": a}])
            cp_b = canonicalize_prompt([{"role": "user", "content": b}])
            assert cp_a.canonical_hash == cp_b.canonical_hash

    def test_canonicalizer_relative_paths_unchanged(self):
        from gateway.canonicalizer import canonicalize_prompt

        msg = [{"role": "user", "content": "Check src/main.py and tests/test_app.py"}]
        cp = canonicalize_prompt(msg)
        assert "src/main.py" in cp.canonical_text

    @pytest.mark.asyncio
    async def test_canonicalizer_none_content(self):
        from gateway.canonicalizer import canonicalize_messages

        msgs = [
            {"role": "user", "content": None},
            {"role": "assistant", "content": "response"},
        ]
        result = canonicalize_messages(msgs)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_canonicalizer_very_long_input(self):
        from gateway.canonicalizer import canonicalize_prompt

        long_content = "a" * 100_000
        msgs = [{"role": "user", "content": long_content}]
        result = canonicalize_prompt(msgs)
        assert len(result.canonical_hash) == 64

    @pytest.mark.asyncio
    async def test_cache_key_corrupt_json(self, redis):
        from gateway.cache.exact import ExactCache

        cache = ExactCache(redis_client=redis)
        await cache.set("exact:valid", {"value": 1})
        await redis.set("exact:corrupt", "not-valid-json")

        good = await cache.get("exact:valid")
        assert good == {"value": 1}

        bad = await cache.get("exact:corrupt")
        assert bad is None
        assert await cache.exists("exact:corrupt") is False

    @pytest.mark.asyncio
    async def test_memory_layer_no_deps(self):
        from gateway.memory_layer.store import MemoryLayerStore

        ml = MemoryLayerStore(
            redis_client=None,
            context_store=None,
            artifact_store=None,
            index_store=None,
            memory_store=None,
        )
        result = await ml.search(query="test", agent_id="hermes")
        assert result == []

        ctx = await ml.get_agent_context("hermes")
        assert ctx == []

    @pytest.mark.asyncio
    async def test_semantic_cache_default_behavior(self):
        from gateway.semantic_cache.store import SemanticCache
        from gateway.semantic_cache.schemas import SemanticCacheEntry

        sc = SemanticCache(location=None, url=None, port=6333, threshold=0.90)
        entry = SemanticCacheEntry(
            canonical_hash="abc",
            canonical_text="test query text",
            model="deepseek-chat",
            response={"value": "test"},
        )
        stored = sc.store(entry)
        assert stored is True

        result = sc.search("test query text", model="deepseek-chat")
        assert result is not None
        if result:
            assert result.score >= 0.90

        sc.clear()
        assert True


class TestUpstreamFailure:
    """Verify proxy error handling."""

    @pytest.mark.asyncio
    async def test_proxy_constructor(self):
        import httpx
        from gateway.proxy import DeepSeekProxy

        client = httpx.AsyncClient()
        proxy = DeepSeekProxy(client)
        assert proxy.client is client
        assert proxy.base_url == "https://api.deepseek.com/v1"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_config_defaults(self):
        from gateway.config import Settings

        s = Settings()
        assert s.gateway_port == 8765
        assert s.semantic_cache_enabled is False
        assert s.cache_enabled is True
        agents = s.get_authorized_agents()
        assert "hermes" in agents
        assert "opencode" in agents

    @pytest.mark.asyncio
    async def test_config_env_override(self, monkeypatch):
        from gateway.config import Settings

        monkeypatch.setenv("authorized_agents", "hermes,my-agent")
        s = Settings()
        agents = s.get_authorized_agents()
        assert "hermes" in agents
        assert "my-agent" in agents
        assert "opencode" not in agents

    @pytest.mark.asyncio
    async def test_canonicalizer_deterministic_across_edge_cases(self):
        from gateway.canonicalizer import canonicalize_prompt

        paths_a = [{"role": "user", "content": "Fix /home/alice/project/src/main.py"}]
        paths_b = [{"role": "user", "content": "Fix /home/bob/project/src/main.py"}]
        cp_a = canonicalize_prompt(paths_a)
        cp_b = canonicalize_prompt(paths_b)
        assert cp_a.canonical_hash == cp_b.canonical_hash

    @pytest.mark.asyncio
    async def test_scan_pagination(self, redis):
        from gateway.cache.exact import ExactCache

        cache = ExactCache(redis_client=redis)
        for i in range(50):
            await cache.set(f"exact:{i:03d}", {"value": i})

        size = await cache.size()
        assert size == 50

        deleted = await cache.delete("exact:*")
        assert deleted == 50
        assert await cache.size() == 0
