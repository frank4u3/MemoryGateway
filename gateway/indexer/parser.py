import os
import re as _re
from pathlib import Path
from typing import Optional

from tree_sitter_languages import get_language, get_parser

from gateway.indexer.schemas import CodeSymbol

_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".hh": "cpp",
    ".cxx": "cpp",
    ".hxx": "cpp",
}

_SUPPORTED_EXTS = set(_LANGUAGE_MAP.keys())

_BLOCKLIST_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    ".env",
    "target",
    "build",
    "dist",
    ".idea",
    ".vscode",
    ".DS_Store",
}


def supported_language_for(path: str) -> Optional[str]:
    ext = Path(path).suffix.lower()
    return _LANGUAGE_MAP.get(ext)


def iter_source_files(root: str, max_size_bytes: int = 1_048_576):
    root_path = Path(root).resolve()
    for entry in root_path.rglob("*"):
        if entry.is_dir() and entry.name.startswith(
            "."
        ) or entry.name in _BLOCKLIST_DIRS:
            continue
        if entry.is_file() and entry.suffix.lower() in _SUPPORTED_EXTS:
            if entry.stat().st_size > max_size_bytes:
                continue
            rel = str(entry.relative_to(root_path))
            try:
                content = entry.read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception:
                continue
            yield rel, content


# ---------------------------------------------------------------------------
# tree-sitter queries per language
# ---------------------------------------------------------------------------

_QUERIES: dict[str, dict[str, str]] = {
    "python": {
        "class": """
            (class_definition
              name: (identifier) @name
              body: (block) @body) @node
        """,
        "function": """
            (function_definition
              name: (identifier) @name
              body: (block) @body) @node
        """,
        "import": """
            (import_statement
              name: (dotted_name) @name) @node
        """,
        "import_from": """
            (import_from_statement
              module_name: (dotted_name) @name) @node
        """,
    },
    "javascript": {
        "class": """
            (class_declaration
              name: (identifier) @name
              body: (class_body) @body) @node
        """,
        "function": """
            [
              (function_declaration
                name: (identifier) @name
                body: (statement_block) @body) @node
              (method_definition
                name: (property_identifier) @name
                body: (statement_block) @body) @node
            ]
        """,
        "import": """
            (import_statement
              source: (string) @name) @node
        """,
    },
    "typescript": {
        "class": """
            (class_declaration
              name: (type_identifier) @name
              body: (class_body) @body) @node
        """,
        "function": """
            [
              (function_declaration
                name: (identifier) @name
                body: (statement_block) @body) @node
              (method_definition
                name: (property_identifier) @name
                body: (statement_block) @body) @node
            ]
        """,
        "import": """
            (import_statement
              source: (string) @name) @node
        """,
    },
    "rust": {
        "class": """
            (struct_item
              name: (type_identifier) @name
              body: (struct_body) @body) @node
        """,
        "function": """
            (function_item
              name: (identifier) @name
              body: (block) @body) @node
        """,
        "import": """
            (use_declaration
              argument: (use_list
                (use_parameter (identifier) @name)?)) @node
        """,
    },
    "go": {
        "class": """
            (type_declaration
              (type_spec
                name: (type_identifier) @name
                type: (_) @body)) @node
        """,
        "function": """
            (function_declaration
              name: (identifier) @name
              body: (block) @body) @node
        """,
        "import": """
            (import_declaration
              (import_spec
                path: (interpreted_string_literal) @name)) @node
        """,
    },
    "java": {
        "class": """
            (class_declaration
              name: (identifier) @name
              body: (class_body) @body) @node
        """,
        "function": """
            (method_declaration
              name: (identifier) @name
              body: (block) @body) @node
        """,
        "import": """
            (import_declaration
              name: (scoped_identifier) @name) @node
        """,
    },
    "c": {
        "function": """
            (function_definition
              declarator: (function_declarator
                declarator: (identifier) @name)
              body: (compound_statement) @body) @node
        """,
        "import": """
            (preproc_include
              path: (string_literal) @name) @node
        """,
    },
    "cpp": {
        "class": """
            (class_specifier
              name: (type_identifier) @name
              body: (field_declaration_list) @body) @node
        """,
        "function": """
            (function_definition
              declarator: (function_declarator
                declarator: (identifier) @name)
              body: (compound_statement) @body) @node
        """,
        "import": """
            (preproc_include
              path: (string_literal) @name) @node
        """,
    },
}

# ---------------------------------------------------------------------------
# Cached parsers and languages
# ---------------------------------------------------------------------------

_PARSER_CACHE: dict[str, object] = {}
_LANGUAGE_CACHE: dict[str, object] = {}
_DLL_PATH: Optional[str] = None


def _init_dll_path():
    global _DLL_PATH
    if _DLL_PATH is not None:
        return
    import tree_sitter_languages

    pkg_dir = Path(tree_sitter_languages.__file__).parent
    dll = pkg_dir / "languages.dll"
    if dll.exists():
        _DLL_PATH = str(dll)


def _get_language(lang_name: str):
    if lang_name not in _LANGUAGE_CACHE:
        _init_dll_path()
        lang = get_language(lang_name)
        _LANGUAGE_CACHE[lang_name] = lang
    return _LANGUAGE_CACHE[lang_name]


def _get_parser(lang_name: str):
    if lang_name not in _PARSER_CACHE:
        parser = get_parser(lang_name)
        _PARSER_CACHE[lang_name] = parser
    return _PARSER_CACHE[lang_name]


# ---------------------------------------------------------------------------
# Capture grouping helpers
# ---------------------------------------------------------------------------


def _node_text(node, source_bytes: bytes) -> str:
    """Safely extract text from a node."""
    return source_bytes[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )


def _node_range(node):
    return node.start_point[0] + 1, node.end_point[0] + 1


def _contains_node(parent, child):
    return (
        parent.start_byte <= child.start_byte
        and child.end_byte <= parent.end_byte
        and parent is not child
    )


def _group_captures(
    captures: list,
    node_tag: str = "node",
    name_tag: str = "name",
    body_tag: str = "body",
    source_bytes: bytes = b"",
) -> list:
    """Group flat (Node, tag) captures by enclosing @node."""
    nodes: list[tuple[object, str, object]] = []

    node_list = [n for n, t in captures if t == node_tag]

    for node in node_list:
        name = ""
        body_raw = None
        for n, t in captures:
            if t == name_tag and _contains_node(node, n):
                name = _node_text(n, source_bytes)
            elif t == body_tag and _contains_node(node, n):
                body_raw = n
        nodes.append((node, name, body_raw))

    return nodes


def _extract_docstring(node, source_bytes: bytes) -> str:
    for child in node.children:
        if child.type in ("comment", "line_comment", "block_comment"):
            text = _node_text(child, source_bytes).strip()
            text = text.lstrip("#/;").strip()
            if text:
                return text
        if child.type in ("expression_statement",):
            for grandchild in child.children:
                if grandchild.type in ("string", "string_literal"):
                    text = _node_text(grandchild, source_bytes)
                    text = _re.sub(
                        r'^["\']+|["\']+$', "", text
                    ).strip()
                    if text:
                        return text
    return ""


def _make_file_summary(content: str) -> str:
    lines = content.splitlines()
    summary_parts = []
    for line in lines[:10]:
        s = line.strip()
        if s.startswith(("#", "//", "/*", "*", "*/", "--", '"""', "'''")):
            cleaned = s.lstrip("#;/* -").strip().strip('"').strip("'")
            if cleaned:
                summary_parts.append(cleaned)
    return " ".join(summary_parts)[:200] if summary_parts else Path(
        "__unknown__"
    ).name


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_file(file_path: str, content: str) -> list[CodeSymbol]:
    ext = Path(file_path).suffix.lower()
    lang_name = _LANGUAGE_MAP.get(ext)
    if lang_name is None:
        return []

    symbols: list[CodeSymbol] = []
    source_bytes = content.encode("utf-8")
    parser = _get_parser(lang_name)
    tree = parser.parse(source_bytes)
    lang_obj = _get_language(lang_name)

    queries = _QUERIES.get(lang_name, {})

    # --- File-level symbol ---
    file_summary = _make_file_summary(content)
    symbols.append(
        CodeSymbol(
            file_path=file_path,
            symbol_name=Path(file_path).name,
            symbol_type="file",
            summary=file_summary,
            language=lang_name,
            start_line=1,
            end_line=len(content.splitlines()),
            code_snippet=_truncate(content, 200),
        )
    )

    # --- Classes ---
    if "class" in queries:
        try:
            q = lang_obj.query(queries["class"])
            groups = _group_captures(q.captures(tree.root_node), source_bytes=source_bytes)
            for node, name, body_node in groups:
                summary = (
                    _extract_docstring(node, source_bytes) or name
                )
                s, e = _node_range(node)
                symbols.append(
                    CodeSymbol(
                        file_path=file_path,
                        symbol_name=name,
                        symbol_type="class",
                        summary=summary,
                        language=lang_name,
                        start_line=s,
                        end_line=e,
                        code_snippet=_truncate(
                            _node_text(node, source_bytes), 200
                        ),
                    )
                )
        except Exception:
            pass

    # --- Functions ---
    if "function" in queries:
        try:
            q = lang_obj.query(queries["function"])
            groups = _group_captures(q.captures(tree.root_node), source_bytes=source_bytes)
            for node, name, body_node in groups:
                summary = (
                    _extract_docstring(node, source_bytes) or name
                )
                s, e = _node_range(node)
                symbols.append(
                    CodeSymbol(
                        file_path=file_path,
                        symbol_name=name,
                        symbol_type="function",
                        summary=summary,
                        language=lang_name,
                        start_line=s,
                        end_line=e,
                        code_snippet=_truncate(
                            _node_text(node, source_bytes), 200
                        ),
                    )
                )
        except Exception:
            pass

    # --- Imports ---
    for import_key in ("import", "import_from"):
        if import_key in queries:
            try:
                q = lang_obj.query(queries[import_key])
                seen: set[str] = set()
                for node, tag in q.captures(tree.root_node):
                    if tag == "node":
                        txt = _node_text(node, source_bytes).strip()
                        if txt and txt not in seen:
                            seen.add(txt)
                            s, e = _node_range(node)
                            symbols.append(
                                CodeSymbol(
                                    file_path=file_path,
                                    symbol_name=txt.splitlines()[0][
                                        :120
                                    ],
                                    symbol_type="import",
                                    summary=txt.splitlines()[0][:200],
                                    language=lang_name,
                                    start_line=s,
                                    end_line=e,
                                    code_snippet=txt,
                                )
                            )
            except Exception:
                pass

    return symbols


def index_repository(path: str) -> list[CodeSymbol]:
    all_symbols: list[CodeSymbol] = []
    for rel_path, content in iter_source_files(path):
        symbols = parse_file(rel_path, content)
        for sym in symbols:
            sym.file_path = rel_path
        all_symbols.extend(symbols)
    return all_symbols
