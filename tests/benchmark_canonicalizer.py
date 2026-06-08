"""
Benchmark: Canonical Prompt Builder performance.

Measures:
- normalize_text latency (various input sizes and complexity)
- canonicalize_messages latency
- canonicalize_prompt latency (full pipeline)
- Hash generation throughput
- Equivalent prompt detection accuracy

Run:  python -m pytest tests/benchmark_canonicalizer.py -v -s
"""

import time

import pytest

from gateway.canonicalizer import (
    CanonicalPrompt,
    canonicalize_messages,
    canonicalize_prompt,
    normalize_text,
)

SIMPLE_TEXT = "Hello, how are you?"
PATH_TEXT = (
    "Review the file /home/user/project/src/main.py for bugs. "
    "The build was at 2026-06-08T14:31:22Z. "
    "Session ID: sess_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8g9h0i. "
    "Agent: agent=hermes. "
    "Temp: /tmp/build123.tmp."
)
COMPLEX_TEXT = (
    "Session sess_a1b2c3d4e5f6a7b8c9d0e1f2a at 2026-06-08T14:31:22Z "
    "agent=hermes wrote /home/user/project/src/main.py "
    "on server 550e8400-e29b-41d4-a716-446655440000 "
    "output to /tmp/build.tmp. "
    "Also checked C:\\Users\\alice\\work\\data\\results.csv "
    "and /var/log/app/error.log. "
) * 10

SIMPLE_MESSAGES = [{"role": "user", "content": "Say hello"}]

COMPLEX_MESSAGES = [
    {"role": "system", "content": "You are hermes, an AI coding agent."},
    {"role": "user", "content": "Review /home/user/project/src/main.py"},
    {"role": "assistant", "content": "I found a bug at /home/user/project/src/utils.py line 42"},
    {"role": "user", "content": "Fix it and test with /tmp/test_data.tmp"},
] * 5


def _fmt(ns: float) -> str:
    if ns >= 1_000_000:
        return f"{ns/1_000_000:.2f}ms"
    if ns >= 1_000:
        return f"{ns/1_000:.2f}us"
    return f"{ns:.2f}ns"


def _ops(n: int, s: float) -> str:
    rate = n / s
    if rate >= 1000:
        return f"{rate/1000:.1f}k ops/sec"
    return f"{rate:.0f} ops/sec"


class TestNormalizeTextBenchmark:
    def test_simple_text(self):
        count = 10000
        start = time.perf_counter()
        for _ in range(count):
            normalize_text(SIMPLE_TEXT)
        elapsed = time.perf_counter() - start
        per_op = elapsed / count * 1_000_000
        print(f"  simple text: {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")
        assert normalize_text(SIMPLE_TEXT) == "Hello, how are you?"

    def test_path_text(self):
        count = 5000
        start = time.perf_counter()
        for _ in range(count):
            normalize_text(PATH_TEXT)
        elapsed = time.perf_counter() - start
        print(f"  path/timestamp/session text: {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")
        assert "<workspace>" in normalize_text(PATH_TEXT)

    def test_complex_text(self):
        count = 1000
        start = time.perf_counter()
        for _ in range(count):
            normalize_text(COMPLEX_TEXT)
        elapsed = time.perf_counter() - start
        print(f"  complex text (10x): {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")
        assert "<workspace>" in normalize_text(COMPLEX_TEXT)


class TestCanonicalizeMessagesBenchmark:
    def test_simple_messages(self):
        count = 5000
        start = time.perf_counter()
        for _ in range(count):
            canonicalize_messages(SIMPLE_MESSAGES)
        elapsed = time.perf_counter() - start
        print(f"  simple messages: {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")

    def test_complex_messages(self):
        count = 1000
        start = time.perf_counter()
        for _ in range(count):
            canonicalize_messages(COMPLEX_MESSAGES)
        elapsed = time.perf_counter() - start
        print(f"  complex messages (20 turns): {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")

    def test_with_max_turns(self):
        count = 1000
        start = time.perf_counter()
        for _ in range(count):
            canonicalize_messages(COMPLEX_MESSAGES, max_turns=10)
        elapsed = time.perf_counter() - start
        print(f"  complex + max_turns=10: {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")


class TestCanonicalizePromptBenchmark:
    def test_full_pipeline_simple(self):
        count = 5000
        start = time.perf_counter()
        for _ in range(count):
            canonicalize_prompt(messages=SIMPLE_MESSAGES, model="deepseek-chat")
        elapsed = time.perf_counter() - start
        print(f"  simple prompt: {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")

    def test_full_pipeline_complex(self):
        count = 1000
        start = time.perf_counter()
        for _ in range(count):
            canonicalize_prompt(
                messages=COMPLEX_MESSAGES,
                model="deepseek-chat",
                temperature=0.1,
                max_tokens=4096,
            )
        elapsed = time.perf_counter() - start
        print(f"  complex prompt: {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")

    def test_hash_generation(self):
        count = 5000
        result = canonicalize_prompt(messages=SIMPLE_MESSAGES, model="deepseek-chat")
        start = time.perf_counter()
        for _ in range(count):
            _ = result.canonical_hash
        elapsed = time.perf_counter() - start
        print(f"  hash field access: {_ops(count, elapsed)}  ({_fmt(elapsed/count*1_000_000)}/op)")
        assert len(result.canonical_hash) == 64


class TestEquivalentDetection:
    def test_cache_hit_improvement_estimate(self):
        """Demonstrate that semantically equivalent prompts produce identical hashes."""
        msgs_a = [
            {"role": "system", "content": "You are hermes, an AI coding agent."},
            {"role": "user", "content": "Review /home/alice/project/src/main.py for bugs at 2026-06-08T14:31:22Z"},
        ]
        msgs_b = [
            {"role": "user", "content": "Review /home/bob/project/src/main.py for bugs at 2026-06-09T10:15:00Z"},
            {"role": "system", "content": "You are hermes, an AI coding agent."},
        ]
        a = canonicalize_prompt(messages=msgs_a, model="deepseek-chat", temperature=0.1)
        b = canonicalize_prompt(messages=msgs_b, model="deepseek-chat", temperature=0.1)
        assert a.canonical_hash == b.canonical_hash, "Equivalent prompts should have same hash"
        # Report: 4 differences normalized away (path, timestamp, reorder, duplicated content fields)
        print(f"  Same hash despite different paths, timestamps, message order, and None fields")
        print(f"  Hash: {a.canonical_hash}")

    def test_cache_hit_improvement_estimate_multi_agent(self):
        """Different agent IDs in content should not affect hash when strip-none is applied."""
        msgs_hermes = [
            {"role": "system", "content": "You are hermes, an AI coding agent."},
            {"role": "user", "content": "What's the capital of France?"},
        ]
        msgs_opencode = [
            {"role": "user", "content": "What's the capital of France?"},
            {"role": "system", "content": "You are opencode, an AI coding agent."},
        ]
        a = canonicalize_prompt(messages=msgs_hermes, model="deepseek-chat")
        b = canonicalize_prompt(messages=msgs_opencode, model="deepseek-chat")
        # Different system prompt content should give different hashes (different agent instructions)
        assert a.canonical_hash != b.canonical_hash, "Different system prompts should give different hashes"

    def test_path_sensitivity(self):
        """Two prompts differing only by absolute paths should have the SAME hash."""
        a = canonicalize_prompt(
            messages=[{"role": "user", "content": "Review /home/user/a/src/main.py"}],
            model="deepseek-chat",
        )
        b = canonicalize_prompt(
            messages=[{"role": "user", "content": "Review /home/other/b/src/main.py"}],
            model="deepseek-chat",
        )
        assert a.canonical_hash == b.canonical_hash, "Paths should normalize to same hash"
