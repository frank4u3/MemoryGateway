import asyncio
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


# ---------------------------------------------------------------------------
# Versioning tests
# ---------------------------------------------------------------------------


class TestMemoryPackVersioning:
    def test_create_version_writes_files(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        version_id = v.create_version(
            files={
                "architecture.md": "# Arch",
                "roadmap.md": "# Roadmap",
                "current_state.md": "# State",
                "active_tasks.md": "# Tasks",
            },
            trigger_type="git_commit",
            git_commit_sha="abc123",
            diff_summary="1 file modified",
        )
        assert version_id.startswith("v")
        version_dir = Path(tmpdir) / version_id
        assert version_dir.is_dir()
        assert (version_dir / "architecture.md").read_text() == "# Arch"
        assert (version_dir / "manifest.json").exists()

        manifest = json.loads((version_dir / "manifest.json").read_text())
        assert manifest["trigger_type"] == "git_commit"
        assert manifest["git_commit_sha"] == "abc123"

    def test_create_version_stores_diff_report(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        version_id = v.create_version(
            files={"architecture.md": "# Arch"},
            trigger_type="doc_change",
            diff_report={"summary": "test diff", "total_added": 5},
        )
        version_dir = Path(tmpdir) / version_id
        diff_path = version_dir / "diff_report.json"
        assert diff_path.exists()

        diff_data = json.loads(diff_path.read_text())
        assert diff_data["summary"] == "test diff"
        assert diff_data["total_added"] == 5

    def test_get_current(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        vid = v.create_version(
            files={"architecture.md": "# V1"},
            trigger_type="manual",
        )
        current = v.get_current()
        assert current is not None
        assert current.version_id == vid
        assert current.read_file("architecture.md") == "# V1"

    def test_list_versions_sorted(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        v.create_version(files={"architecture.md": "# 1"}, trigger_type="manual")
        import time
        time.sleep(1.1)
        v.create_version(files={"architecture.md": "# 2"}, trigger_type="manual")
        time.sleep(1.1)
        v.create_version(files={"architecture.md": "# 3"}, trigger_type="manual")

        versions = v.list_versions()
        assert len(versions) == 3
        assert versions[0]["trigger_type"] == "manual"

    def test_rollback(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning
        import time

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        v1 = v.create_version(files={"architecture.md": "# V1"})
        time.sleep(1.1)
        v2 = v.create_version(files={"architecture.md": "# V2"})

        assert v.get_current_version_id() == v2
        assert v.rollback(v1) is True
        assert v.get_current_version_id() == v1

        current = v.get_current()
        assert current.read_file("architecture.md") == "# V1"

    def test_rollback_nonexistent(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        v.create_version(files={"architecture.md": "# V1"})
        assert v.rollback("nonexistent") is False

    def test_get_version(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        vid = v.create_version(
            files={
                "architecture.md": "# Arch",
                "roadmap.md": "# Roadmap",
            },
            trigger_type="arch_change",
            diff_report={"summary": "arch diff"},
        )

        mpv = v.get_version(vid)
        assert mpv is not None
        assert mpv.read_diff_report() is not None
        assert mpv.read_diff_report()["summary"] == "arch diff"

    def test_get_version_nonexistent(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        assert v.get_version("nonexistent") is None

    def test_no_current_before_creation(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        assert v.get_current() is None
        assert v.get_current_version_id() is None


# ---------------------------------------------------------------------------
# Diff tests
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_files_identical(self):
        from gateway.memory.diff import diff_files

        content = "line1\nline2\nline3"
        fd = diff_files(content, content, filename="test.md")
        assert fd.status == "unchanged"
        assert fd.lines_added == 0
        assert fd.lines_removed == 0
        assert fd.lines_changed == 0

    def test_diff_files_added_lines(self):
        from gateway.memory.diff import diff_files

        old_content = "line1\n"
        new_content = "line1\nline2\nline3\n"
        fd = diff_files(old_content, new_content, filename="test.md")
        assert fd.status == "modified"
        assert fd.lines_added >= 2
        assert "line2" in fd.unified_diff or "+" in fd.unified_diff

    def test_diff_files_removed_lines(self):
        from gateway.memory.diff import diff_files

        old_content = "line1\nline2\nline3\n"
        new_content = "line1\n"
        fd = diff_files(old_content, new_content, filename="test.md")
        assert fd.status == "modified"
        assert fd.lines_removed >= 2

    def test_diff_files_section_detection(self):
        from gateway.memory.diff import diff_files

        old_content = "# Old Header\ncontent\n"
        new_content = "# New Header\ncontent\n"
        fd = diff_files(old_content, new_content, filename="test.md")
        assert fd.status == "modified"
        assert "# New Header" in fd.sections_changed

    def test_diff_packs_new_file(self):
        from gateway.memory.diff import diff_packs

        report = diff_packs(
            old_files={},
            new_files={"new.md": "# New File\ncontent\n"},
            from_version="none",
            to_version="v1",
        )
        assert report.summary.startswith("Diff from none to v1")
        assert report.files[0].status == "added"

    def test_diff_packs_unchanged_file(self):
        from gateway.memory.diff import diff_packs

        report = diff_packs(
            old_files={"a.md": "# Same\n"},
            new_files={"a.md": "# Same\n"},
        )
        assert report.files[0].status == "unchanged"
        assert "no changes" in report.summary

    def test_diff_packs_modified_file(self):
        from gateway.memory.diff import diff_packs

        report = diff_packs(
            old_files={"a.md": "# Old\n"},
            new_files={"a.md": "# New\nmore\n"},
        )
        assert report.files[0].status == "modified"
        assert report.total_added > 0

    def test_diff_packs_multiple_files(self):
        from gateway.memory.diff import diff_packs

        report = diff_packs(
            old_files={"a.md": "# A", "b.md": "# B"},
            new_files={"a.md": "# A modified", "b.md": "# B", "c.md": "# C"},
        )
        statuses = {f.filename: f.status for f in report.files}
        assert statuses["a.md"] == "modified"
        assert statuses["b.md"] == "unchanged"
        assert statuses["c.md"] == "added"

    def test_diff_packs_model_dump(self):
        from gateway.memory.diff import diff_packs

        report = diff_packs(
            old_files={"a.md": "# Old\n"},
            new_files={"a.md": "# New\n"},
        )
        dumped = report.model_dump()
        assert dumped["from_version"] == report.from_version
        assert dumped["to_version"] == report.to_version
        assert len(dumped["files"]) == 1
        assert "unified_diff" in dumped["files"][0]

    def test_diff_files_unchanged_no_changes(self):
        from gateway.memory.diff import diff_files

        fd = diff_files("", "", filename="empty.md")
        assert fd.status == "unchanged"
        assert fd.lines_added == 0
        assert fd.lines_removed == 0
        assert fd.lines_changed == 0

    def test_diff_packs_from_none(self):
        from gateway.memory.diff import diff_packs

        report = diff_packs(
            old_files={},
            new_files={},
            from_version="none",
            to_version="v1",
        )
        assert report.summary
        assert report.total_added == 0
        assert report.total_removed == 0


# ---------------------------------------------------------------------------
# Auto-maintenance tests
# ---------------------------------------------------------------------------


class TestAutoMaintenance:
    def test_trigger_config_defaults(self):
        from gateway.memory.auto_maintenance import TriggerConfig

        cfg = TriggerConfig()
        assert cfg.on_git_commit is True
        assert cfg.on_doc_change is True
        assert cfg.on_arch_change is True
        assert cfg.on_dep_change is True
        assert cfg.min_interval_seconds == 60

    def test_trigger_config_custom(self):
        from gateway.memory.auto_maintenance import TriggerConfig

        cfg = TriggerConfig(
            on_git_commit=False,
            on_doc_change=False,
            min_interval_seconds=10,
        )
        assert cfg.on_git_commit is False
        assert cfg.on_doc_change is False
        assert cfg.on_arch_change is True
        assert cfg.min_interval_seconds == 10

    def test_service_creation(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning
        from gateway.memory.generator import MemoryPackGenerator
        from gateway.memory.auto_maintenance import AutoMaintenanceService, TriggerConfig

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        g = MemoryPackGenerator()
        cfg = TriggerConfig(min_interval_seconds=0)
        svc = AutoMaintenanceService(
            versioning=v,
            generator=g,
            trigger_config=cfg,
            repo_path=".",
        )
        assert svc.last_version_id == ""
        assert svc.last_trigger_type == ""

    def test_handle_trigger_disabled(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning
        from gateway.memory.generator import MemoryPackGenerator
        from gateway.memory.auto_maintenance import AutoMaintenanceService, TriggerConfig

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        g = MemoryPackGenerator()
        cfg = TriggerConfig(on_git_commit=False, min_interval_seconds=0)
        svc = AutoMaintenanceService(
            versioning=v,
            generator=g,
            trigger_config=cfg,
        )
        result = asyncio.run(svc.handle_trigger("git_commit"))
        assert result is None

    def test_generate_now_via_handle(self, tmpdir):
        from gateway.memory.versioning import MemoryPackVersioning
        from gateway.memory.generator import MemoryPackGenerator
        from gateway.memory.auto_maintenance import AutoMaintenanceService, TriggerConfig

        class MockGenerator(MemoryPackGenerator):
            async def generate(self, repo_path=".", previous_files=None, git_diff_summary="", project_structure=""):
                return {
                    "architecture.md": "# Mock Architecture",
                    "roadmap.md": "# Mock Roadmap",
                    "current_state.md": "# Mock State",
                    "active_tasks.md": "# Mock Tasks",
                }

        v = MemoryPackVersioning(base_dir=str(tmpdir))
        g = MockGenerator()
        svc = AutoMaintenanceService(
            versioning=v,
            generator=g,
            trigger_config=TriggerConfig(min_interval_seconds=0),
        )
        version_id = asyncio.run(svc.generate_now("manual"))
        assert version_id.startswith("v")
        assert v.get_current_version_id() == version_id

    def test_install_git_hook_no_git(self, tmpdir):
        from gateway.memory.auto_maintenance import AutoMaintenanceService

        repo = Path(str(tmpdir))
        result = AutoMaintenanceService.install_git_hook(str(repo))
        assert result is False

    def test_install_git_hook_with_git(self, tmpdir):
        from gateway.memory.auto_maintenance import AutoMaintenanceService

        repo = Path(str(tmpdir))
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        scripts = repo / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "post-commit").write_text("#!/bin/bash\necho test", encoding="utf-8")

        result = AutoMaintenanceService.install_git_hook(str(repo))
        assert result is True
        assert (hooks / "post-commit").is_file()


# ---------------------------------------------------------------------------
# File watcher tests
# ---------------------------------------------------------------------------


class TestFileWatcher:
    def test_watcher_creation(self, tmpdir):
        from gateway.memory.watcher import FileWatcher

        triggered = []

        async def on_trigger(tt):
            triggered.append(tt)

        w = FileWatcher(
            repo_path=str(tmpdir),
            on_trigger=on_trigger,
            poll_interval_seconds=0.1,
            cooldown_seconds=0,
        )
        assert w._running is False

    def test_watcher_start_stop(self, tmpdir):
        from gateway.memory.watcher import FileWatcher

        triggered = []

        async def on_trigger(tt):
            triggered.append(tt)

        w = FileWatcher(
            repo_path=str(tmpdir),
            on_trigger=on_trigger,
            poll_interval_seconds=0.1,
            cooldown_seconds=1,
        )
        asyncio.run(w.start())
        assert w._running is True
        asyncio.run(w.stop())
        assert w._running is False

    def test_watcher_detect_doc_change(self, tmpdir):
        from gateway.memory.watcher import FileWatcher

        triggered = []

        async def on_trigger(tt):
            triggered.append(tt)

        repo = Path(str(tmpdir))
        (repo / "README.md").write_text("# Initial", encoding="utf-8")

        w = FileWatcher(
            repo_path=str(repo),
            on_trigger=on_trigger,
            poll_interval_seconds=0.1,
            cooldown_seconds=0,
            debounce_seconds=0,
        )

        async def test_flow():
            await w.start()
            await asyncio.sleep(0.2)
            (repo / "README.md").write_text("# Modified", encoding="utf-8")
            await asyncio.sleep(0.4)
            await w.stop()

        asyncio.run(test_flow())
        assert "doc_change" in triggered, f"Expected doc_change trigger, got {triggered}"

    def test_watcher_detect_dep_change(self, tmpdir):
        from gateway.memory.watcher import FileWatcher

        triggered = []

        async def on_trigger(tt):
            triggered.append(tt)

        repo = Path(str(tmpdir))
        (repo / "requirements.txt").write_text("initial", encoding="utf-8")

        w = FileWatcher(
            repo_path=str(repo),
            on_trigger=on_trigger,
            poll_interval_seconds=0.1,
            cooldown_seconds=0,
            debounce_seconds=0,
        )

        async def test_flow():
            await w.start()
            await asyncio.sleep(0.2)
            (repo / "requirements.txt").write_text("fastapi==1.0", encoding="utf-8")
            await asyncio.sleep(0.4)
            await w.stop()

        asyncio.run(test_flow())
        assert "dep_change" in triggered, f"Expected dep_change trigger, got {triggered}"

    def test_watcher_noise_ignored(self, tmpdir):
        from gateway.memory.watcher import FileWatcher

        triggered = []

        async def on_trigger(tt):
            triggered.append(tt)

        repo = Path(str(tmpdir))
        w = FileWatcher(
            repo_path=str(repo),
            on_trigger=on_trigger,
            poll_interval_seconds=0.1,
            cooldown_seconds=0,
            debounce_seconds=0,
        )

        async def test_flow():
            await w.start()
            await asyncio.sleep(0.2)
            (repo / "main.py").write_text("x = 1", encoding="utf-8")
            await asyncio.sleep(0.4)
            await w.stop()

        asyncio.run(test_flow())
        assert len(triggered) == 0, f"Expected no triggers for non-matching file, got {triggered}"

    def test_watcher_disabled_trigger(self, tmpdir):
        from gateway.memory.watcher import FileWatcher

        triggered = []

        async def on_trigger(tt):
            triggered.append(tt)

        repo = Path(str(tmpdir))
        w = FileWatcher(
            repo_path=str(repo),
            on_trigger=on_trigger,
            poll_interval_seconds=0.1,
            cooldown_seconds=0,
            debounce_seconds=0,
            enabled_triggers=set(),
        )

        async def test_flow():
            await w.start()
            await asyncio.sleep(0.2)
            (repo / "README.md").write_text("# Test", encoding="utf-8")
            await asyncio.sleep(0.4)
            await w.stop()

        asyncio.run(test_flow())
        assert len(triggered) == 0, f"Expected no triggers when all disabled, got {triggered}"
