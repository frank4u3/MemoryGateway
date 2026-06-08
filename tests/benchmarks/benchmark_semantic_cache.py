"""Benchmarks for the semantic cache.

Run with: python -m pytest tests/benchmarks/benchmark_semantic_cache.py -v --benchmark-only
"""

import pytest

from gateway.semantic_cache.schemas import SemanticCacheEntry
from gateway.semantic_cache.store import create_semantic_cache


@pytest.fixture
def cache():
    c = create_semantic_cache(in_memory=True, threshold=0.98)
    yield c
    c.clear()


def _make_entry(hash_: str, text: str) -> SemanticCacheEntry:
    return SemanticCacheEntry(
        canonical_hash=hash_,
        canonical_text=text,
        model="deepseek-chat",
        response={"choices": [{"message": {"content": text[::-1]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    )


def test_benchmark_store_single(cache, benchmark):
    entry = _make_entry("h1", "What is the capital of France?")

    def _run():
        cache.store(entry)

    benchmark(_run)


def test_benchmark_search_empty(cache, benchmark):
    def _run():
        cache.search(
            canonical_text="What is the capital of France?",
            model="deepseek-chat",
        )

    benchmark(_run)


def test_benchmark_store_and_search_single(cache, benchmark):
    entry = _make_entry("h1", "What is the capital of France?")
    cache.store(entry)

    def _run():
        cache.search(
            canonical_text="What is the capital of France?",
            model="deepseek-chat",
        )

    benchmark(_run)


def test_benchmark_store_100_entries(cache, benchmark):
    entries = [_make_entry(f"h{i}", f"This is test entry number {i} with some unique content") for i in range(100)]
    for e in entries:
        cache.store(e)

    def _run():
        cache.search(
            canonical_text="test entry number 42",
            model="deepseek-chat",
        )

    benchmark(_run)


def test_benchmark_search_similar(cache, benchmark):
    cache.store(_make_entry("h1", "Python is a great programming language for data science"))

    def _run():
        cache.search(
            canonical_text="Python is an excellent language for data science work",
            model="deepseek-chat",
        )

    benchmark(_run)


def test_benchmark_store_batch(cache, benchmark):
    entries = [_make_entry(f"h{i}", f"Entry number {i} with varied content for testing purposes") for i in range(50)]

    def _run():
        for e in entries:
            cache.store(e)

    benchmark(_run)


def test_benchmark_search_no_match(cache, benchmark):
    cache.store(_make_entry("h1", "The weather is nice today in Paris"))

    def _run():
        cache.search(
            canonical_text="Quantum entanglement in particle physics",
            model="deepseek-chat",
        )

    benchmark(_run)
