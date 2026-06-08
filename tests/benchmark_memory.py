"""Performance benchmarks for the Project Memory Pack Generator.

Run with:
    pytest tests/benchmark_memory.py --benchmark-only
"""

import tempfile
from pathlib import Path

import pytest

from gateway.memory import MemoryStore, build_pack, generate_pack


def test_bench_generate_simple(benchmark):
    files = [{"path": "main.py", "content": "x = 1\n"}]
    benchmark(generate_pack, files=files)


def test_bench_generate_with_symbols(benchmark):
    files = [
        {"path": "calc.py", "content": "class Calc:\n    def add(self, a, b): return a + b\n"},
        {"path": "utils.py", "content": "def helper(): pass\n"},
        {"path": "README.md", "content": "# Project\nSome docs.\n"},
    ]
    benchmark(generate_pack, files=files)


def test_bench_generate_large_repo(benchmark):
    files = []
    for i in range(100):
        files.append({
            "path": f"mod{i}/file{i}.py",
            "content": f"class Class{i}:\n    def method(self): pass\n",
        })
    files.append({"path": "README.md", "content": "# Large Project\n"})
    benchmark(generate_pack, files=files)


def test_bench_build_pack(benchmark):
    files = {f"f{i}.md": f"# File {i}\n" for i in range(50)}
    benchmark(build_pack, files)


def test_bench_store_save(benchmark):
    store = MemoryStore(base_dir=str(Path(tempfile.mkdtemp())))
    pack = build_pack({"a.md": "a" * 1000})
    benchmark(store.save, pack)


def test_bench_store_load(benchmark, tmpdir):
    store = MemoryStore(base_dir=str(tmpdir))
    store.save(build_pack({"a.md": "a"}))
    benchmark(store.load, "current")


def test_bench_write_then_read(benchmark):
    store = MemoryStore(base_dir=str(Path(tempfile.mkdtemp())))

    def write_read():
        pack = build_pack({"f.md": "# Test"}, version="v1")
        store.save(pack)
        store.load("v1")

    benchmark(write_read)
