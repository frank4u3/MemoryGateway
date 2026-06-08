"""Performance benchmarks for the Repository Intelligence Indexer.

Run with:
    pytest tests/benchmark_indexer.py --benchmark-only
"""

import pytest

from gateway.indexer.embedder import CodeEmbedder
from gateway.indexer.parser import parse_file
from gateway.indexer.qdrant_store import create_store
from gateway.indexer.schemas import CodeSymbol


# ---------------------------------------------------------------------------
# Embedding benchmarks
# ---------------------------------------------------------------------------


def test_bench_embed_short_text(benchmark):
    embedder = CodeEmbedder()
    benchmark(embedder.embed, "def foo(): pass")


def test_bench_embed_long_text(benchmark):
    embedder = CodeEmbedder()
    text = "class " + "A" * 1000
    benchmark(embedder.embed, text)


def test_bench_embed_batch_10(benchmark):
    embedder = CodeEmbedder()
    texts = [f"def func{i}(): pass" for i in range(10)]
    benchmark(embedder.embed_batch, texts)


def test_bench_embed_batch_100(benchmark):
    embedder = CodeEmbedder()
    texts = [f"def func{i}(): pass" for i in range(100)]
    benchmark(embedder.embed_batch, texts)


# ---------------------------------------------------------------------------
# Parsing benchmarks
# ---------------------------------------------------------------------------


def test_bench_parse_small_python(benchmark):
    code = """
def hello():
    return "world"

class Foo:
    def bar(self):
        pass
"""
    benchmark(parse_file, "small.py", code)


def test_bench_parse_large_python(benchmark):
    lines = []
    for i in range(50):
        lines.append(f"def func_{i}(a, b):\n    return a + b\n")
    for i in range(20):
        lines.append(
            f"class Class_{i}:\n    def method(self): pass\n"
        )
    code = "".join(lines)
    benchmark(parse_file, "large.py", code)


def test_bench_parse_javascript(benchmark):
    code = """
function add(a, b) {
    return a + b;
}
class Calculator {
    multiply(x, y) {
        return x * y;
    }
}
"""
    benchmark(parse_file, "calc.js", code)


# ---------------------------------------------------------------------------
# Qdrant store benchmarks
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    s = create_store(in_memory=True)
    yield s
    s.delete_collection()


def test_bench_index_single_symbol(benchmark, store):
    sym = CodeSymbol(
        file_path="test.py",
        symbol_name="test",
        symbol_type="function",
        summary="A test function",
        language="python",
        code_snippet="def test(): pass",
    )
    benchmark(store.index_symbols, [sym])


def test_bench_index_batch_100(benchmark, store):
    symbols = [
        CodeSymbol(
            file_path=f"mod{i}.py",
            symbol_name=f"func{i}",
            symbol_type="function",
            summary=f"Function {i}",
            language="python",
            code_snippet=f"def func{i}(): return {i}",
        )
        for i in range(100)
    ]
    benchmark(store.index_symbols, symbols)


def test_bench_search_small_index(benchmark, store):
    syms = [
        CodeSymbol(
            file_path=f"mod{i}.py",
            symbol_name=f"func{i}",
            symbol_type="function",
            summary=f"Function {i}",
            language="python",
        )
        for i in range(10)
    ]
    store.index_symbols(syms)
    benchmark(store.search, "function", 5)


def test_bench_search_large_index(benchmark, store):
    syms = [
        CodeSymbol(
            file_path=f"mod{i}.py",
            symbol_name=f"func{i}",
            symbol_type="function",
            summary=f"Function {i}",
            language="python",
        )
        for i in range(1000)
    ]
    store.index_symbols(syms)
    benchmark(store.search, "function", 10)


# ---------------------------------------------------------------------------
# Full pipeline benchmarks
# ---------------------------------------------------------------------------


def test_bench_index_search_roundtrip(benchmark):
    store = create_store(in_memory=True)
    code = """
class Calculator:
    def add(self, a, b):
        return a + b
    def multiply(self, a, b):
        return a * b

def version():
    return "1.0"
"""
    symbols = parse_file("calc.py", code)

    def pipeline():
        store.index_symbols(symbols)
        return store.search("calculator add multiply", 5)

    benchmark(pipeline)
    store.delete_collection()


def test_bench_embed_then_search(benchmark, store):
    embedder = CodeEmbedder()
    sym = CodeSymbol(
        file_path="test.py",
        symbol_name="test",
        symbol_type="function",
        summary="test",
        language="python",
    )
    store.index_symbols([sym])

    def embed_and_search():
        embedder.embed("test function")
        store.search("test function", 5)

    benchmark(embed_and_search)
    store.delete_collection()
