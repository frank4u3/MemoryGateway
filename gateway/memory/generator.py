"""Generates project memory pack files from repository content."""

import os
import re as _re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from gateway.indexer.parser import (
    parse_file,
    iter_source_files,
    supported_language_for,
)


def generate_pack(
    path: Optional[str] = None,
    files: Optional[list[dict]] = None,
) -> dict[str, str]:
    """Generate all six memory pack files, returning {filename: content}."""
    repo_files: dict[str, str] = {}

    if path:
        for rel, content in iter_source_files(path):
            repo_files[rel] = content
        for rel, content in _iter_doc_files(path):
            repo_files[rel] = content
    elif files:
        for f in files:
            repo_files[f.get("path", "")] = f.get("content", "")
    else:
        repo_files = _scan_cwd()

    return _generate_all(repo_files)


def _scan_cwd() -> dict[str, str]:
    """Scan current working directory for relevant files."""
    result: dict[str, str] = {}
    root = Path.cwd().resolve()

    # Source files
    for rel_path, content in iter_source_files(str(root)):
        result[rel_path] = content

    # Doc / config files
    for pattern in (
        "*.md",
        "*.mdx",
        "*.toml",
        "*.cfg",
        "*.ini",
        "*.yaml",
        "*.yml",
        "*.json",
        "*.txt",
        "Dockerfile",
        "*.dockerfile",
        "Makefile",
        "*.mk",
    ):
        for f in root.glob(pattern):
            if f.is_file() and f.stat().st_size < 1_048_576:
                try:
                    rel = str(f.relative_to(root))
                    result[rel] = f.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except Exception:
                    pass

    for f in root.rglob("*"):
        if not f.is_file():
            continue
        name = f.name
        if name.startswith(".") and name not in (
            ".env.example",
        ):
            continue
        if f.suffix.lower() not in (
            ".md",
            ".mdx",
            ".toml",
            ".yaml",
            ".yml",
            ".json",
        ):
            continue
        try:
            rel = str(f.relative_to(root))
            if rel not in result:
                result[rel] = f.read_text(
                    encoding="utf-8", errors="replace"
                )
        except Exception:
            pass

    return result


def _iter_doc_files(root: str):
    """Yield (rel_path, content) for documentation files."""
    root_path = Path(root).resolve()
    for entry in root_path.rglob("*"):
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        name = entry.name.lower()
        if ext in (
            ".md",
            ".mdx",
            ".rst",
            ".txt",
        ) or name in (
            "readme",
            "readme.md",
            "contributing",
            "contributing.md",
            "changelog",
            "changelog.md",
            "license",
        ):
            try:
                rel = str(entry.relative_to(root_path))
                content = entry.read_text(
                    encoding="utf-8", errors="replace"
                )
                yield rel, content
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Content generators
# ---------------------------------------------------------------------------

_FILENAMES = [
    "architecture.md",
    "roadmap.md",
    "current_state.md",
    "coding_rules.md",
    "active_tasks.md",
    "repo_summary.md",
]


def _generate_all(repo_files: dict[str, str]) -> dict[str, str]:
    ctx = _analyze(repo_files)
    return {
        "architecture.md": _generate_architecture(ctx),
        "roadmap.md": _generate_roadmap(ctx),
        "current_state.md": _generate_current_state(ctx),
        "coding_rules.md": _generate_coding_rules(ctx),
        "active_tasks.md": _generate_active_tasks(ctx),
        "repo_summary.md": _generate_repo_summary(ctx),
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class _Context:
    def __init__(self):
        self.repo_files: dict[str, str] = {}
        self.doc_files: dict[str, str] = {}
        self.source_files: dict[str, str] = {}
        self.config_files: dict[str, str] = {}
        self.symbols_by_file: dict[str, list] = {}
        self.languages: set[str] = set()
        self.total_lines = 0
        self.module_dirs: list[str] = []
        self.dependencies: dict[str, set[str]] = defaultdict(set)
        self.tasks: list[str] = []
        self.readme_content = ""
        self.architecture_content = ""
        self.roadmap_content = ""
        self.adr_content = ""
        self.migration_content = ""
        self.has_tests = False
        self.has_docker = False
        self.has_ci = False


def _analyze(repo_files: dict[str, str]) -> _Context:
    ctx = _Context()
    ctx.repo_files = repo_files

    for path, content in repo_files.items():
        low = path.lower()

        if low.endswith(".md"):
            ctx.doc_files[path] = content
            if "readme" in low:
                ctx.readme_content = content
            elif "architecture" in low or "arch" in low:
                ctx.architecture_content += content + "\n"
            elif "roadmap" in low:
                ctx.roadmap_content += content + "\n"
            elif "adr" in low or "decision" in low:
                ctx.adr_content += content + "\n"
            elif "migration" in low:
                ctx.migration_content += content + "\n"

        elif supported_language_for(path):
            ctx.source_files[path] = content

        elif low.endswith(
            (".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".env.example")
        ):
            ctx.config_files[path] = content

    # Parse source files for symbols
    for path, content in ctx.source_files.items():
        try:
            symbols = parse_file(path, content)
            ctx.symbols_by_file[path] = symbols
            lang = symbols[0].language if symbols else "unknown"
            ctx.languages.add(lang)
            ctx.total_lines += len(content.splitlines())
        except Exception:
            pass

    # Detect module directories from file paths
    dir_counts: dict[str, int] = defaultdict(int)
    for path in ctx.source_files:
        parts = Path(path).parts
        if len(parts) > 1:
            dir_counts[parts[0]] += 1
    ctx.module_dirs = sorted(
        [d for d, c in dir_counts.items() if c > 1]
    )

    # Dependencies from config files
    if "pyproject.toml" in repo_files:
        ctx.dependencies["python"].update(
            _extract_python_deps(repo_files["pyproject.toml"])
        )
    if "requirements.txt" in repo_files:
        ctx.dependencies["python"].update(
            _extract_requirements_deps(repo_files["requirements.txt"])
        )
    if "package.json" in repo_files:
        ctx.dependencies["node"].update(
            _extract_node_deps(repo_files["package.json"])
        )
    if "Cargo.toml" in repo_files:
        ctx.dependencies["rust"].update(
            _extract_cargo_deps(repo_files["Cargo.toml"])
        )
    if "go.mod" in repo_files:
        ctx.dependencies["go"].update(
            _extract_go_deps(repo_files["go.mod"])
        )

    # Infra detection
    ctx.has_tests = any(
        "test" in Path(p).name.lower() for p in repo_files
    )
    ctx.has_docker = any(
        "docker" in Path(p).name.lower() for p in repo_files
    )
    ctx.has_ci = any(
        ".github" in p or ".gitlab" in p or "ci" in p
        for p in repo_files
    )

    # Find task-like content
    ctx.tasks = _extract_tasks(repo_files)

    return ctx


def _extract_python_deps(content: str) -> set[str]:
    deps = set()
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('"') and ">" in line:
            name = line.split(">")[0].strip('"').strip()
            if name:
                deps.add(name)
    return deps


def _extract_requirements_deps(content: str) -> set[str]:
    deps = set()
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name = line.split(">")[0].split("=")[0].split("<")[0].strip()
            if name and not name.startswith("-"):
                deps.add(name)
    return deps


def _extract_node_deps(content: str) -> set[str]:
    import json

    try:
        data = json.loads(content)
        deps = set(data.get("dependencies", {}).keys())
        deps.update(data.get("devDependencies", {}).keys())
        return deps
    except Exception:
        return set()


def _extract_cargo_deps(content: str) -> set[str]:
    import re

    deps = set()
    for m in re.finditer(r'^(\w[\w-]+)\s*=\s*["{]', content, re.MULTILINE):
        deps.add(m.group(1))
    return deps


def _extract_go_deps(content: str) -> set[str]:
    deps = set()
    for line in content.splitlines():
        line = line.strip()
        if (
            line
            and not line.startswith("module ")
            and not line.startswith("go ")
        ):
            parts = line.split()
            if parts and "/" in parts[0]:
                deps.add(parts[0])
    return deps


def _extract_tasks(repo_files: dict[str, str]) -> list[str]:
    tasks = []
    task_patterns = [
        r"(?:TODO|FIXME|HACK|XXX|BUG|OPTIMIZE)\s*[:]\s*(.+)",
        r"- \[ \]\s*(.+)",
        r"- \[x\]\s*(.+)",
    ]
    for path, content in repo_files.items():
        if path.endswith(
            (".py", ".js", ".ts", ".rs", ".go", ".java", ".md")
        ):
            for pattern in task_patterns:
                for m in _re.finditer(
                    pattern, content, _re.IGNORECASE
                ):
                    tasks.append(m.group(1).strip()[:120])
    return tasks[:50]


# ---------------------------------------------------------------------------
# Markdown generators
# ---------------------------------------------------------------------------


def _generate_architecture(ctx: _Context) -> str:
    lines = [
        "# Architecture",
        "",
        "## Project Structure",
        "",
    ]

    # Directory tree
    tree = _build_tree(ctx.repo_files)
    for line in tree:
        lines.append(f"    {line}")
    lines.append("")

    # Languages
    lines.append("## Languages")
    lines.append("")
    for lang in sorted(ctx.languages):
        count = sum(
            1
            for s in ctx.symbols_by_file.values()
            if s and s[0].language == lang
        )
        lines.append(f"- **{lang.capitalize()}**: {count}+ files")
    lines.append("")

    # Modules
    if ctx.module_dirs:
        lines.append("## Modules")
        lines.append("")
        for mod in ctx.module_dirs:
            symbols = ctx.symbols_by_file.get(mod, [])
            classes = [
                s.symbol_name
                for s in symbols
                if s.symbol_type == "class"
            ]
            funcs = [
                s.symbol_name
                for s in symbols
                if s.symbol_type == "function"
            ]
            lines.append(f"### {mod}/")
            if classes:
                lines.append(f"- Classes: {', '.join(classes[:10])}")
            if funcs:
                lines.append(f"- Functions: {', '.join(funcs[:10])}")
            lines.append("")

    # Dependencies
    if ctx.dependencies:
        lines.append("## Dependencies")
        lines.append("")
        for eco, deps in sorted(ctx.dependencies.items()):
            lines.append(f"### {eco.capitalize()}")
            for d in sorted(deps)[:30]:
                lines.append(f"- {d}")
            lines.append("")

    # Key files
    lines.append("## Key Files")
    lines.append("")
    key_sections = [
        ("Config", ctx.config_files),
        ("Documentation", ctx.doc_files),
    ]
    for label, files in key_sections:
        lines.append(f"### {label}")
        for f in sorted(files)[:10]:
            lines.append(f"- `{f}`")
        lines.append("")

    return "\n".join(lines)


def _generate_roadmap(ctx: _Context) -> str:
    lines = [
        "# Roadmap",
        "",
        "> Auto-generated from project documentation and task tracking.",
        "",
    ]

    if ctx.architecture_content:
        lines.append("## Architecture Documents")
        lines.append("")
        lines.append("Key points from architecture documentation:")
        lines.append("")
        lines.append("```")
        for line in ctx.architecture_content.splitlines()[:20]:
            lines.append(line)
        lines.append("```")
        lines.append("")

    if ctx.roadmap_content:
        lines.append("## Roadmap Documents")
        lines.append("")
        for line in ctx.roadmap_content.splitlines()[:30]:
            if line.strip().startswith("#"):
                lines.append(line)
            elif line.strip():
                stripped = line.strip()
                if any(
                    c in stripped
                    for c in (
                        "phase",
                        "milestone",
                        "v0.",
                        "v1.",
                        "todo",
                        "next",
                        "goal",
                    )
                ):
                    lines.append(f"- {stripped}")
        lines.append("")

    if ctx.adr_content:
        lines.append("## Architecture Decision Records")
        lines.append("")
        for line in ctx.adr_content.splitlines()[:20]:
            if line.strip().startswith("#") or line.strip().startswith(
                "-"
            ):
                lines.append(line)
        lines.append("")

    if ctx.migration_content:
        lines.append("## Migration Guide")
        lines.append("")
        for line in ctx.migration_content.splitlines()[:15]:
            lines.append(line)
        lines.append("")

    # Tasks that look like goals
    goal_keywords = [
        "implement",
        "add",
        "create",
        "build",
        "refactor",
        "migrate",
        "upgrade",
        "support",
    ]
    goal_tasks = [
        t
        for t in ctx.tasks
        if any(kw in t.lower() for kw in goal_keywords)
    ]
    if goal_tasks:
        lines.append("## Planned Work")
        lines.append("")
        for task in goal_tasks[:20]:
            lines.append(f"- [ ] {task}")
        lines.append("")

    return "\n".join(lines)


def _generate_current_state(ctx: _Context) -> str:
    source_count = len(ctx.source_files)
    doc_count = len(ctx.doc_files)
    config_count = len(ctx.config_files)

    lines = [
        "# Current State",
        "",
        "## Overview",
        "",
        f"- **Source files**: {source_count}",
        f"- **Documentation files**: {doc_count}",
        f"- **Config files**: {config_count}",
        f"- **Total lines**: {ctx.total_lines:,}",
        f"- **Languages**: {', '.join(sorted(ctx.languages))}",
        f"- **Infrastructure**: Docker={ctx.has_docker}, CI={ctx.has_ci}, Tests={ctx.has_tests}",
        "",
        "## Source Files by Type",
        "",
    ]

    ext_counts: dict[str, int] = defaultdict(int)
    for p in ctx.source_files:
        ext_counts[Path(p).suffix.lower() or "(no ext)"] += 1
    for ext, count in sorted(ext_counts.items()):
        lines.append(f"- `{ext}`: {count} files")
    lines.append("")

    # Symbol counts
    class_count = 0
    func_count = 0
    import_count = 0
    for symbols in ctx.symbols_by_file.values():
        for sym in symbols:
            if sym.symbol_type == "class":
                class_count += 1
            elif sym.symbol_type == "function":
                func_count += 1
            elif sym.symbol_type == "import":
                import_count += 1

    lines.append("## Code Symbols")
    lines.append("")
    lines.append(f"- **Classes**: {class_count}")
    lines.append(f"- **Functions**: {func_count}")
    lines.append(f"- **Imports**: {import_count}")
    lines.append("")

    return "\n".join(lines)


def _generate_coding_rules(ctx: _Context) -> str:
    lines = [
        "# Coding Rules",
        "",
        "## Conventions Detected",
        "",
    ]

    # Detect patterns from source files
    naming_conventions = _detect_naming(ctx)
    lines.append("### Naming Conventions")
    lines.append("")
    for convention, examples in naming_conventions.items():
        lines.append(f"- **{convention}**")
        for ex in examples[:3]:
            lines.append(f"  - `{ex}`")
    lines.append("")

    # Project-specific patterns from docs
    if ctx.readme_content:
        lines.append("## Project Conventions")
        lines.append("")
        for line in ctx.readme_content.splitlines():
            if any(
                kw in line.lower()
                for kw in (
                    "convention",
                    "style",
                    "rule",
                    "should",
                    "must",
                    "require",
                    "prefer",
                    "avoid",
                )
            ):
                stripped = line.strip().lstrip("#* \t")
                if stripped:
                    lines.append(f"- {stripped}")
        lines.append("")

    # TODO/FIXME density
    todo_count = len(ctx.tasks)
    lines.append("## Code Health")
    lines.append("")
    lines.append(f"- **TODO/FIXME markers**: {todo_count}")
    lines.append("")

    # Language-specific rules from config
    lines.append("## Language-Specific")
    lines.append("")
    for lang in sorted(ctx.languages):
        lines.append(f"### {lang.capitalize()}")
        lines.append("")
        if lang == "python":
            lines.append("- Follow PEP 8")
            lines.append("- Use type hints")
            lines.append("- Prefer `pathlib` over `os.path`")
            lines.append("- Use `pathlib.Path` for file paths")
        elif lang in ("javascript", "typescript"):
            lines.append("- Use ES6+ syntax")
            lines.append("- Prefer `const` over `let`")
            lines.append("- Use async/await over callbacks")
        elif lang == "rust":
            lines.append("- Follow Rust edition conventions")
            lines.append("- Use `clippy` for linting")
        elif lang == "go":
            lines.append("- Follow `gofmt` conventions")
            lines.append("- Use error returns over exceptions")
        lines.append("")

    return "\n".join(lines)


def _generate_active_tasks(ctx: _Context) -> str:
    lines = [
        "# Active Tasks",
        "",
    ]

    if ctx.tasks:
        lines.append("## Extracted from Source")
        lines.append("")
        for task in ctx.tasks:
            lines.append(f"- [ ] {task}")
        lines.append("")

    # Detect phases from documentation
    lines.append("## Recent Changes")
    lines.append("")
    lines.append("- _Auto-detected from task annotations_")
    lines.append("")

    return "\n".join(lines)


def _generate_repo_summary(ctx: _Context) -> str:
    source_count = len(ctx.source_files)
    doc_count = len(ctx.doc_files)

    lines = [
        "# Repository Summary",
        "",
        "## Identity",
        "",
    ]

    # Project name from README or repo context
    project_name = Path.cwd().resolve().name
    if ctx.readme_content:
        first_line = ctx.readme_content.splitlines()[0].strip()
        if first_line.startswith("#"):
            project_name = first_line.lstrip("#").strip()
    lines.append(f"- **Project**: {project_name}")
    lines.append(f"- **Source files**: {source_count}")
    lines.append(f"- **Document files**: {doc_count}")
    lines.append(f"- **Languages**: {', '.join(sorted(ctx.languages))}")
    lines.append("")

    # File overview
    lines.append("## File Inventory")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|----------|-------|")
    lines.append(f"| Source | {source_count} |")
    lines.append(f"| Documentation | {doc_count} |")
    lines.append(f"| Configuration | {len(ctx.config_files)} |")
    lines.append("")

    # Module breakdown
    if ctx.module_dirs:
        lines.append("## Module Structure")
        lines.append("")
        lines.append("```")
        for mod in ctx.module_dirs:
            lines.append(f"{mod}/")
            for f in sorted(ctx.source_files):
                if f.startswith(mod):
                    lines.append(f"  {f}")
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_tree(files: dict[str, str]) -> list[str]:
    paths = sorted(Path(p) for p in files)
    tree_lines: list[str] = []
    tree_lines.append(".")

    # Build tree structure
    dirs_seen: set[str] = set()
    for p in paths:
        parts = p.parts
        for i in range(1, len(parts)):
            parent = str(Path(*parts[:i]))
            if parent not in dirs_seen:
                dirs_seen.add(parent)
                tree_lines.append(f"  {'  ' * (i-1)}{parts[i-1]}/")
        tree_lines.append(f"  {'  ' * (len(parts)-1)}{parts[-1]}")

    return tree_lines


def _detect_naming(ctx: _Context) -> dict[str, list[str]]:
    conventions: dict[str, list[str]] = defaultdict(list)

    snake_case = _re.compile(r"^[a-z][a-z0-9_]+$")
    camelCase = _re.compile(r"^[a-z][a-zA-Z0-9]+$")
    PascalCase = _re.compile(r"^[A-Z][a-zA-Z0-9]+$")
    SCREAMING = _re.compile(r"^[A-Z][A-Z0-9_]+$")

    for symbols in ctx.symbols_by_file.values():
        for sym in symbols:
            name = sym.symbol_name
            if snake_case.match(name):
                conventions["snake_case"].append(name)
            elif camelCase.match(name):
                conventions["camelCase"].append(name)
            elif PascalCase.match(name):
                conventions["PascalCase"].append(name)
            elif SCREAMING.match(name):
                conventions["SCREAMING_SNAKE_CASE"].append(name)

    return {
        k: sorted(set(v))[:10] for k, v in conventions.items()
    }


# ---------------------------------------------------------------------------
# AI-powered Memory Pack Generator
# ---------------------------------------------------------------------------

import asyncio
import json as _json
import subprocess
from typing import Optional

import httpx

from gateway.config import settings
from gateway.logger import get_logger as _get_logger

_pack_logger = _get_logger()

# The four files the auto-maintenance system tracks
AUTO_PACK_FILES = [
    "architecture.md",
    "roadmap.md",
    "current_state.md",
    "active_tasks.md",
]


class MemoryPackGenerator:
    """Generates memory pack content using the gateway's own DeepSeek proxy.

    This is self-referential: the gateway calls its own /v1/chat/completions
    endpoint to generate updated memory pack files.
    """

    def __init__(
        self,
        gateway_base_url: str = f"http://localhost:{settings.gateway_port}",
        auth_token: str = "",
    ):
        self._base_url = gateway_base_url.rstrip("/")
        self._auth_token = auth_token or "auto"

    # ---- Public API ----

    async def generate(
        self,
        repo_path: str = ".",
        previous_files: Optional[dict[str, str]] = None,
        git_diff_summary: str = "",
        project_structure: str = "",
    ) -> dict[str, str]:
        """Generate all four memory pack files using the AI proxy.

        Returns {filename: content} for the four auto pack files.
        """
        _pack_logger.info("memory_pack_generator_start")

        # Gather context
        git_info = self._get_git_info(repo_path)
        if not project_structure:
            project_structure = self._get_project_structure(repo_path)
        if not git_diff_summary:
            git_diff_summary = self._get_git_diff_summary(repo_path)

        # Build the generation prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            project_structure=project_structure,
            git_diff_summary=git_diff_summary,
            git_info=git_info,
            previous_files=previous_files or {},
        )

        # Call the gateway's own chat completions endpoint
        try:
            content = await self._call_proxy(system_prompt, user_prompt)
        except Exception as exc:
            _pack_logger.error(
                "memory_pack_generator_proxy_error",
                extra={"error": str(exc)},
            )
            raise

        # Parse the response into separate files
        files = self._parse_response(content)
        _pack_logger.info(
            "memory_pack_generator_complete",
            extra={"files_generated": list(files.keys())},
        )
        return files

    # ---- Git helpers ----

    def _get_git_info(self, repo_path: str) -> dict:
        """Get recent git commit info."""
        info = {"recent_commits": [], "current_sha": "", "branch": ""}
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
            )
            if result.returncode == 0:
                info["recent_commits"] = [
                    line.strip() for line in result.stdout.strip().splitlines()
                    if line.strip()
                ]
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
            )
            if result.returncode == 0:
                info["current_sha"] = result.stdout.strip()
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
            )
            if result.returncode == 0:
                info["branch"] = result.stdout.strip()
        except Exception:
            pass

        return info

    def _get_git_diff_summary(self, repo_path: str) -> str:
        """Get a summary of recent changes."""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat", "HEAD~1", "HEAD"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return "No recent diff available."

    def _get_project_structure(self, repo_path: str) -> str:
        """Get a summary of the project file structure."""
        try:
            root = Path(repo_path).resolve()
            lines = []
            skip = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox"}
            for entry in sorted(root.rglob("*")):
                if any(part in skip for part in entry.parts):
                    continue
                if entry.is_file():
                    try:
                        rel = str(entry.relative_to(root))
                        if len(rel) < 120:
                            lines.append(rel)
                    except Exception:
                        pass
                if len(lines) > 200:
                    lines.append("... (truncated)")
                    break
            return "\n".join(lines)
        except Exception:
            return "Unable to scan project structure."

    # ---- Prompt building ----

    def _build_system_prompt(self) -> str:
        return (
            "You are a technical documentation assistant. "
            "Generate memory pack files for a software project. "
            "Return EXACTLY four markdown files separated by the marker "
            "---FILE:filename.md--- on its own line. "
            "The four files must be: architecture.md, roadmap.md, "
            "current_state.md, active_tasks.md. "
            "Each file must be valid markdown. Be concise but thorough. "
            "Do not include any text outside the file markers."
        )

    def _build_user_prompt(
        self,
        project_structure: str,
        git_diff_summary: str,
        git_info: dict,
        previous_files: dict[str, str],
    ) -> str:
        parts = [
            "Generate updated memory pack files for this project.",
            "",
            "## Project Structure",
            "```",
            project_structure[:3000],
            "```",
            "",
            "## Recent Git Changes",
            "```",
            git_diff_summary[:2000],
            "```",
            "",
            f"## Current Branch: {git_info.get('branch', 'unknown')}",
            f"## Current Commit: {git_info.get('current_sha', 'unknown')[:12]}",
            "",
            "## Recent Commits",
        ]
        for commit in git_info.get("recent_commits", [])[:10]:
            parts.append(f"- {commit}")

        if previous_files:
            parts.append("")
            parts.append("## Previous Memory Pack Content (for reference)")
            for fn, content in previous_files.items():
                parts.append(f"### {fn}")
                parts.append("```")
                parts.append(content[:1500])
                parts.append("```")

        parts.append("")
        parts.append(
            "Now generate the four memory pack files. "
            "Use the marker ---FILE:filename.md--- before each file."
        )
        return "\n".join(parts)

    # ---- Proxy call ----

    async def _call_proxy(self, system_prompt: str, user_prompt: str) -> str:
        """Call the gateway's own /v1/chat/completions endpoint."""
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
            "temperature": 0.2,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._auth_token}",
            "X-Agent-ID": "hermes",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("Empty response from proxy")

        return choices[0].get("message", {}).get("content", "")

    # ---- Response parsing ----

    def _parse_response(self, content: str) -> dict[str, str]:
        """Parse the AI response into separate files."""
        files: dict[str, str] = {}
        current_file = None
        current_lines = []

        for line in content.splitlines():
            if line.startswith("---FILE:") and line.endswith("---"):
                if current_file and current_lines:
                    files[current_file] = "\n".join(current_lines).strip()
                current_file = line[len("---FILE:"):-len("---")].strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_file and current_lines:
            files[current_file] = "\n".join(current_lines).strip()

        # Ensure all expected files exist (even if empty)
        for fn in AUTO_PACK_FILES:
            if fn not in files:
                files[fn] = f"# {fn.replace('.md', '').replace('_', ' ').title()}\n\n*Auto-generation did not produce this file.*"

        return files
