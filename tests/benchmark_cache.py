"""
Benchmark: Exact Cache performance.

Measures:
- Cache set throughput (ops/sec)
- Cache get (hit) throughput
- Cache get (miss) throughput
- Full request pipeline with and without cache

Run:  python -m pytest tests/benchmark_cache.py -v -s
"""

import asyncio
import time

import pytest
from fakeredis import FakeAsyncRedis

from gateway.cache.exact import ExactCache, generate_cache_key

SAMPLE_MESSAGES = [
    [{"role": "user", "content": "What is the capital of France?"}],
    [{"role": "user", "content": "Explain Python decorators"}],
    [{"role": "system", "content": "Be concise"}, {"role": "user", "content": "How does Redis work?"}],
]

SAMPLE_RESPONSE = {
    "id": "chatcmpl-bench",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "deepseek-chat",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Paris is the capital of France."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
}


def _gen_keys(count: int) -> list[str]:
    return [
        generate_cache_key("deepseek-chat", msgs)
        for msgs in (SAMPLE_MESSAGES * (count // len(SAMPLE_MESSAGES) + 1))[:count]
    ]


def _format_ops(ops: float) -> str:
    if ops >= 1000:
        return f"{ops/1000:.1f}k ops/sec"
    return f"{ops:.0f} ops/sec"


def _measure(coro_func, iterations: int = 1):
    async def run():
        start = time.perf_counter()
        result = await coro_func()
        elapsed = time.perf_counter() - start
        return result, elapsed

    return asyncio.run(run())


async def _run_bench(name: str, coro_func, count: int):
    start = time.perf_counter()
    result = await coro_func()
    elapsed = time.perf_counter() - start
    ops = count / elapsed
    print(f"  {name}: {count} ops in {elapsed:.3f}s = {_format_ops(ops)}")
    return result


@pytest.mark.asyncio
async def test_cache_set_throughput():
    redis = FakeAsyncRedis(decode_responses=True)
    cache = ExactCache(redis, ttl=3600)
    keys = _gen_keys(200)

    async def run():
        for key in keys:
            await cache.set(key, SAMPLE_RESPONSE)
        return len(keys)

    result = await _run_bench("set", run, 200)
    assert result == 200
    await redis.aclose()


@pytest.mark.asyncio
async def test_cache_get_hit_throughput():
    redis = FakeAsyncRedis(decode_responses=True)
    cache = ExactCache(redis, ttl=3600)
    keys = _gen_keys(200)
    for key in keys:
        await cache.set(key, SAMPLE_RESPONSE)

    async def run():
        for key in keys:
            await cache.get(key)
        return len(keys)

    result = await _run_bench("get (hit)", run, 200)
    assert result == 200
    await redis.aclose()


@pytest.mark.asyncio
async def test_cache_get_miss_throughput():
    redis = FakeAsyncRedis(decode_responses=True)
    cache = ExactCache(redis, ttl=3600)
    keys = _gen_keys(200)

    async def run():
        for key in keys:
            await cache.get(key)
        return len(keys)

    result = await _run_bench("get (miss)", run, 200)
    assert result == 200
    await redis.aclose()


@pytest.mark.asyncio
async def test_cache_set_get_roundtrip_latency():
    redis = FakeAsyncRedis(decode_responses=True)
    cache = ExactCache(redis, ttl=3600)
    key = generate_cache_key("deepseek-chat", SAMPLE_MESSAGES[0])

    async def roundtrip():
        await cache.set(key, SAMPLE_RESPONSE)
        return await cache.get(key)

    start = time.perf_counter()
    result = await roundtrip()
    elapsed = time.perf_counter() - start
    print(f"  roundtrip latency: {elapsed*1000:.2f}ms")
    assert result is not None
    assert result["choices"][0]["message"]["content"] == SAMPLE_RESPONSE["choices"][0]["message"]["content"]
    await redis.aclose()


@pytest.mark.asyncio
async def test_cache_bulk_write_then_read_bench():
    """Full pipeline: generate keys, write entries, read them all back."""
    redis = FakeAsyncRedis(decode_responses=True)
    cache = ExactCache(redis, ttl=3600)
    keys = _gen_keys(500)

    async def bulk():
        for key in keys:
            await cache.set(key, SAMPLE_RESPONSE)
        results = []
        for key in keys:
            r = await cache.get(key)
            if r:
                results.append(r)
        return len(results)

    result = await _run_bench("bulk write+read", bulk, 500)
    assert result == 500
    await redis.aclose()


def test_pure_key_generation_bench():
    """SHA256 key generation is not async, benchmark directly."""
    msgs = SAMPLE_MESSAGES[0]
    count = 5000

    start = time.perf_counter()
    for _ in range(count):
        generate_cache_key("deepseek-chat", msgs)
    elapsed = time.perf_counter() - start
    ops = count / elapsed
    print(f"  key generation: {count} ops in {elapsed:.3f}s = {_format_ops(ops)}")

    result = generate_cache_key("deepseek-chat", msgs)
    assert result.startswith("exact:")


@pytest.mark.asyncio
async def test_concurrent_cache_access():
    """Simulate 10 concurrent agents hitting the cache simultaneously."""
    redis = FakeAsyncRedis(decode_responses=True)
    cache = ExactCache(redis, ttl=3600)
    keys = _gen_keys(100)
    for key in keys:
        await cache.set(key, SAMPLE_RESPONSE)

    async def worker(key: str):
        return await cache.get(key)

    async def concurrent():
        tasks = [worker(key) for key in keys]
        results = await asyncio.gather(*tasks)
        return len([r for r in results if r is not None])

    result = await _run_bench("concurrent (100)", concurrent, 100)
    assert result == 100
    await redis.aclose()
