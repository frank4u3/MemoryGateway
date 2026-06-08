import pytest
import redis.asyncio as aioredis
from fakeredis import FakeAsyncRedis

from gateway.cache.exact import ExactCache, generate_cache_key, tokens_saved_from_cached


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def cache(redis):
    return ExactCache(redis, ttl=60)


class TestGenerateCacheKey:
    def test_same_input_same_key(self):
        msgs = [{"role": "user", "content": "Hello"}]
        k1 = generate_cache_key("deepseek-chat", msgs)
        k2 = generate_cache_key("deepseek-chat", msgs)
        assert k1 == k2
        assert k1.startswith("exact:")

    def test_different_model_different_key(self):
        msgs = [{"role": "user", "content": "Hello"}]
        k1 = generate_cache_key("deepseek-chat", msgs)
        k2 = generate_cache_key("deepseek-coder", msgs)
        assert k1 != k2

    def test_different_messages_different_key(self):
        k1 = generate_cache_key("deepseek-chat", [{"role": "user", "content": "Hi"}])
        k2 = generate_cache_key(
            "deepseek-chat", [{"role": "user", "content": "Hello"}]
        )
        assert k1 != k2

    def test_temperature_affects_key(self):
        msgs = [{"role": "user", "content": "Hi"}]
        k1 = generate_cache_key("deepseek-chat", msgs, temperature=0.1)
        k2 = generate_cache_key("deepseek-chat", msgs, temperature=0.5)
        assert k1 != k2

    def test_max_tokens_affects_key(self):
        msgs = [{"role": "user", "content": "Hi"}]
        k1 = generate_cache_key("deepseek-chat", msgs, max_tokens=100)
        k2 = generate_cache_key("deepseek-chat", msgs, max_tokens=200)
        assert k1 != k2

    def test_key_format(self):
        msgs = [{"role": "user", "content": "Hello"}]
        key = generate_cache_key("deepseek-chat", msgs)
        assert key.startswith("exact:")
        assert len(key) == len("exact:") + 64  # SHA256 hex = 64 chars


class TestExactCache:
    async def test_set_and_get(self, cache, redis):
        await cache.set("exact:test1", {"choices": [{"index": 0, "message": {"content": "Hi"}}]})
        result = await cache.get("exact:test1")
        assert result is not None
        assert result["choices"][0]["message"]["content"] == "Hi"

    async def test_get_miss(self, cache):
        result = await cache.get("exact:nonexistent")
        assert result is None

    async def test_set_with_ttl(self, cache, redis):
        await cache.set("exact:ttltest", {"data": "hello"})
        remaining = await cache.key_ttl("exact:ttltest")
        assert 0 < remaining <= 60

    async def test_delete_all(self, cache):
        await cache.set("exact:a", {"v": 1})
        await cache.set("exact:b", {"v": 2})
        deleted = await cache.delete("exact:*")
        assert deleted == 2

    async def test_delete_specific(self, cache):
        await cache.set("exact:a", {"v": 1})
        await cache.set("exact:b", {"v": 2})
        deleted = await cache.delete("exact:a")
        assert deleted == 1
        assert await cache.exists("exact:b")

    async def test_exists(self, cache):
        await cache.set("exact:exists_test", {"v": 1})
        assert await cache.exists("exact:exists_test") is True
        assert await cache.exists("exact:no_exists") is False

    async def test_size(self, cache):
        assert await cache.size() == 0
        await cache.set("exact:s1", {"v": 1})
        await cache.set("exact:s2", {"v": 2})
        assert await cache.size() == 2

    async def test_ping(self, cache):
        latency = await cache.ping()
        assert latency >= 0

    async def test_corrupt_entry_removed(self, cache, redis):
        await redis.set("exact:corrupt", "not-json")
        result = await cache.get("exact:corrupt")
        assert result is None
        assert await cache.exists("exact:corrupt") is False


class TestTokensSaved:
    def test_returns_prompt_tokens(self):
        resp = {"usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180}}
        assert tokens_saved_from_cached(resp) == 150

    def test_returns_zero_when_no_usage(self):
        assert tokens_saved_from_cached({}) == 0
