import json
from pathlib import Path
import tempfile

import pytest

from gateway.memory import (
    MemoryPack,
    MemoryPackFile,
    MemoryResponse,
    MemoryStore,
    RebuildRequest,
    build_pack,
    generate_pack,
)
from gateway.memory.generator import _FILENAMES


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestMemorySchemas:
    def test_memory_pack_file(self):
        f = MemoryPackFile(filename="test.md", content="# Hello")
        assert f.filename == "test.md"
        assert f.content == "# Hello"

    def test_memory_pack(self):
        pack = MemoryPack(
            version="v1",
            created_at="2026-01-01T00:00:00Z",
            checksum="abc123",
            files=[
                MemoryPackFile(filename="a.md", content="a"),
                MemoryPackFile(filename="b.md", content="b"),
            ],
        )
        assert pack.version == "v1"
        assert len(pack.files) == 2

    def test_rebuild_request_schema(self):
        r = RebuildRequest(path="/tmp/repo")
        assert r.path == "/tmp/repo"
        assert r.files is None

    def test_memory_response_schema(self):
        r = MemoryResponse(
            version="v1",
            created_at="2026-01-01T00:00:00Z",
            checksum="def456",
            file_count=3,
            files={"a.md": "a", "b.md": "b", "c.md": "c"},
        )
        assert r.file_count == 3
        assert r.files["a.md"] == "a"


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestMemoryStore:
    def test_save_and_load_current(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        pack = build_pack({"a.md": "# A", "b.md": "# B"})
        store.save(pack)

        loaded = store.current()
        assert loaded is not None
        assert loaded.version == pack.version
        assert loaded.checksum == pack.checksum
        assert len(loaded.files) == 2

    def test_current_version(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        assert store.current_version() is None

        pack = build_pack({"x.md": "x"})
        store.save(pack)
        assert store.current_version() == pack.version

    def test_load_by_version(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        pack = build_pack({"y.md": "y"}, version="v42")
        store.save(pack)

        loaded = store.load("v42")
        assert loaded is not None
        assert loaded.version == "v42"
        assert loaded.files[0].content == "y"

    def test_load_missing_version(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        assert store.load("nonexistent") is None

    def test_list_versions(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        assert store.list_versions() == []

        p1 = build_pack({"f1.md": "f1"}, version="v1")
        p2 = build_pack({"f2.md": "f2"}, version="v2")
        store.save(p1)
        store.save(p2)

        versions = store.list_versions()
        assert "v1" in versions
        assert "v2" in versions

    def test_multiple_saves_creates_separate_dirs(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        v1 = build_pack({"a.md": "a"}, version="v1")
        v2 = build_pack({"b.md": "b"}, version="v2")
        store.save(v1)
        store.save(v2)

        assert store.load("v1").files[0].content == "a"
        assert store.load("v2").files[0].content == "b"


# ---------------------------------------------------------------------------
# build_pack tests
# ---------------------------------------------------------------------------


class TestBuildPack:
    def test_build_pack_creates_version(self):
        pack = build_pack({"readme.md": "# Project"})
        assert pack.version.startswith("v")
        assert pack.created_at != ""
        assert pack.checksum != ""
        assert len(pack.files) == 1

    def test_build_pack_checksum_deterministic(self):
        p1 = build_pack({"a.md": "hello"}, version="v1")
        p2 = build_pack({"a.md": "hello"}, version="v1")
        assert p1.checksum == p2.checksum

    def test_build_pack_different_content_different_checksum(self):
        p1 = build_pack({"a.md": "hello"}, version="v1")
        p2 = build_pack({"a.md": "world"}, version="v1")
        assert p1.checksum != p2.checksum

    def test_build_pack_sorts_files(self):
        pack = build_pack(
            {"z.md": "z", "a.md": "a"}, version="v1"
        )
        assert pack.files[0].filename == "a.md"

    def test_build_pack_custom_version(self):
        pack = build_pack({"x.md": "x"}, version="v42")
        assert pack.version == "v42"


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------


class TestGeneratePack:
    def test_generate_pack_returns_dict(self):
        result = generate_pack(
            files=[{"path": "test.py", "content": "x = 1\n"}]
        )
        assert isinstance(result, dict)

    def test_generate_pack_has_all_six_files(self):
        result = generate_pack(
            files=[{"path": "test.py", "content": "x = 1\n"}]
        )
        for name in _FILENAMES:
            assert name in result, f"Missing {name}"

    def test_generate_pack_content_is_markdown(self):
        result = generate_pack(
            files=[{"path": "test.py", "content": "x = 1\n"}]
        )
        for name, content in result.items():
            assert content.startswith("# "), (
                f"{name} should start with #"
            )

    def test_generate_pack_with_source_parses_symbols(self):
        files = [
            {
                "path": "calc.py",
                "content": (
                    "class Calculator:\n"
                    '    """Adds numbers."""\n'
                    "    def add(self, a, b): return a + b\n"
                ),
            },
        ]
        result = generate_pack(files=files)
        arch = result.get("architecture.md", "")
        assert "Calculator" in arch or "calc" in arch.lower()

    def test_generate_pack_empty_input(self):
        result = generate_pack(files=[])
        assert len(result) == 6

    def test_generate_pack_readme_detection(self):
        files = [
            {
                "path": "README.md",
                "content": "# My Project\nDescription here.",
            },
        ]
        result = generate_pack(files=files)
        summary = result.get("repo_summary.md", "")
        assert "My Project" in summary

    def test_generate_pack_todo_extraction(self):
        files = [
            {
                "path": "main.py",
                "content": "# TODO: implement login\n# FIXME: fix timeout\n",
            },
        ]
        result = generate_pack(files=files)
        active = result.get("active_tasks.md", "")
        assert "implement login" in active or "login" in active

    def test_generate_pack_with_repo_path(self, tmpdir):
        repo = tmpdir / "myproject"
        repo.mkdir()
        (repo / "main.py").write_text(
            "def hello(): pass\nclass App: pass\n", encoding="utf-8"
        )
        (repo / "README.md").write_text(
            "# Test App\n", encoding="utf-8"
        )

        result = generate_pack(path=str(repo))
        assert len(result) == 6
        summary = result.get("repo_summary.md", "")
        assert "Test App" in summary or "myproject" in summary

    def test_all_outputs_are_unique(self):
        result = generate_pack(
            files=[{"path": "test.py", "content": "x = 1\n"}]
        )
        contents = list(result.values())
        unique = set(contents)
        assert len(unique) == 6, "Each file should have unique content"


# ---------------------------------------------------------------------------
# Integration: store + generator + versioning
# ---------------------------------------------------------------------------


class TestMemoryIntegration:
    def test_save_and_rebuild(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))

        files = generate_pack(
            path=str(Path.cwd())
        )
        pack = build_pack(files)
        store.save(pack)

        current = store.current()
        assert current is not None
        assert current.checksum == pack.checksum

        loaded_via_version = store.load(pack.version)
        assert loaded_via_version is not None
        assert len(loaded_via_version.files) == 6

    def test_no_current_returns_none(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        assert store.current() is None

    def test_versioning_monotonic(self, tmpdir):
        store = MemoryStore(base_dir=str(tmpdir))
        versions = []
        for i in range(3):
            pack = build_pack(
                {f"f{i}.md": str(i)}, version=f"v{i}"
            )
            store.save(pack)
            versions.append(pack.version)
        listed = store.list_versions()
        for v in versions:
            assert v in listed
