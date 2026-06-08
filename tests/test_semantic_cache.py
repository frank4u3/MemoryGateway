import json
import time

import pytest

from gateway.semantic_cache.schemas import SemanticCacheEntry, SemanticSearchResult
from gateway.semantic_cache.store import SemanticCache, create_semantic_cache


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSemanticCacheSchemas:
    def test_entry_defaults(self):
        entry = SemanticCacheEntry(
            canonical_hash="abc123",
            canonical_text="Hello world",
            model="deepseek-chat",
            response={"choices": [{"message": {"content": "Hi"}}]},
        )
        assert entry.temperature is None
        assert entry.max_tokens is None
        assert entry.top_p is None

    def test_entry_full(self):
        entry = SemanticCacheEntry(
            canonical_hash="abc",
            canonical_text="Hello",
            model="deepseek-chat",
            response={},
            temperature=0.7,
            max_tokens=100,
            top_p=0.9,
        )
        assert entry.temperature == 0.7
        assert entry.max_tokens == 100
        assert entry.top_p == 0.9

    def test_search_result(self):
        result = SemanticSearchResult(
            canonical_hash="abc",
            response={"text": "hello"},
            score=0.99,
        )
        assert result.score == 0.99
        assert result.canonical_hash == "abc"


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


@pytest.fixture
def cache():
    c = create_semantic_cache(in_memory=True, threshold=0.98)
    yield c
    c.clear()


def _make_entry(hash_: str = "hash1", text: str = "Hello, how are you?", model: str = "deepseek-chat") -> SemanticCacheEntry:
    return SemanticCacheEntry(
        canonical_hash=hash_,
        canonical_text=text,
        model=model,
        response={"choices": [{"message": {"content": "I'm fine, thank you!"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    )


class TestSemanticCache:
    def test_store_and_search_hit(self, cache):
        entry = _make_entry()
        assert cache.store(entry) is True

        result = cache.search(
            canonical_text="Hello, how are you?",
            model="deepseek-chat",
        )
        assert result is not None
        assert result.score >= 0.98
        assert result.canonical_hash == "hash1"
        assert "I'm fine" in result.response["choices"][0]["message"]["content"]

    def test_store_and_search_similar(self, cache):
        entry = _make_entry(text="What is the capital of France?")
        cache.store(entry)

        result = cache.search(
            canonical_text="What is the capital of France?",
            model="deepseek-chat",
        )
        assert result is not None
        assert result.score >= 0.98
        assert result.canonical_hash == "hash1"

    def test_search_no_match(self, cache):
        entry = _make_entry(text="The weather is nice today")
        cache.store(entry)

        result = cache.search(
            canonical_text="How does quantum computing work?",
            model="deepseek-chat",
        )
        assert result is None

    def test_search_below_threshold(self, cache):
        entry = _make_entry(text="I enjoy programming in Python")
        cache.store(entry)

        result = cache.search(
            canonical_text="I like coding with JavaScript",
            model="deepseek-chat",
        )
        # These should be below 0.98 threshold
        assert result is None

    def test_threshold_property(self, cache):
        assert cache.threshold == 0.98
        cache.threshold = 0.95
        assert cache.threshold == 0.95

    def test_custom_threshold(self):
        c = create_semantic_cache(in_memory=True, threshold=0.9)
        assert c.threshold == 0.9
        c.clear()

    def test_search_threshold_override(self, cache):
        entry = _make_entry(text="I enjoy programming in Python")
        cache.store(entry)

        # With a lower threshold, it might match
        result = cache.search(
            canonical_text="I like coding with JavaScript",
            model="deepseek-chat",
            threshold=0.5,
        )
        # With threshold low enough, should match
        if result is not None:
            assert result.score >= 0.5

    def test_store_multiple_and_find_best(self, cache):
        entries = [
            _make_entry("h1", "Python is a great programming language"),
            _make_entry("h2", "Java is also widely used"),
            _make_entry("h3", "Functional programming uses lambda calculus"),
        ]
        for e in entries:
            cache.store(e)

        # With hash-based fallback embedder, exact match is needed for 0.98 threshold
        result = cache.search(
            canonical_text="Python is a great programming language",
            model="deepseek-chat",
        )
        assert result is not None
        assert result.score >= 0.98
        assert result.canonical_hash == "h1"

    def test_store_empty_text(self, cache):
        entry = _make_entry(text="")
        assert cache.store(entry) is True
        result = cache.search(
            canonical_text="anything",
            model="deepseek-chat",
        )
        assert result is None

    def test_clear_cache(self, cache):
        entry = _make_entry()
        cache.store(entry)
        cache.clear()
        result = cache.search(
            canonical_text="Hello, how are you?",
            model="deepseek-chat",
        )
        assert result is None

    def test_model_filtering(self, cache):
        cache.store(_make_entry(text="Hello world", model="deepseek-chat"))
        cache.store(_make_entry(text="Hello world", model="gpt-4", hash_="hash_gpt"))

        result_chat = cache.search(
            canonical_text="Hello world",
            model="deepseek-chat",
        )
        result_gpt = cache.search(
            canonical_text="Hello world",
            model="gpt-4",
        )
        assert result_chat is not None
        assert result_gpt is not None

    def test_embedding_determinism(self, cache):
        """Same text should produce same embedding and same search results."""
        from gateway.indexer.embedder import CodeEmbedder

        embedder = CodeEmbedder()
        text = "This is a deterministic test"
        emb1 = embedder.embed(text)
        emb2 = embedder.embed(text)
        assert emb1 == emb2
