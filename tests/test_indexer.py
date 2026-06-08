import pytest

from gateway.indexer.embedder import CodeEmbedder
from gateway.indexer.parser import parse_file, supported_language_for
from gateway.indexer.qdrant_store import CodeIndexStore, create_store
from gateway.indexer.schemas import CodeSymbol, SearchResult


# ---------------------------------------------------------------------------
# Embedder tests
# ---------------------------------------------------------------------------


class TestCodeEmbedder:
    def test_embed_returns_384_floats(self):
        embedder = CodeEmbedder()
        vec = embedder.embed("hello world")
        assert len(vec) == CodeEmbedder.DIM
        assert all(isinstance(v, float) for v in vec)

    def test_embed_is_deterministic(self):
        embedder = CodeEmbedder()
        v1 = embedder.embed("def foo(): pass")
        v2 = embedder.embed("def foo(): pass")
        assert v1 == v2

    def test_embed_similar_texts_have_positive_similarity(self):
        embedder = CodeEmbedder()
        v1 = embedder.embed("class UserModel:")
        v2 = embedder.embed("class UserSchema:")
        dot = sum(a * b for a, b in zip(v1, v2))
        # cosine similarity should be > 0 for code snippets
        assert dot > 0.0

    def test_embed_different_texts_lower_similarity(self):
        embedder = CodeEmbedder()
        v1 = embedder.embed("class UserModel:")
        v2 = embedder.embed("import os")
        v3 = embedder.embed("class UserModel:")
        same_dot = sum(a * b for a, b in zip(v1, v3))
        diff_dot = sum(a * b for a, b in zip(v1, v2))
        assert same_dot > diff_dot

    def test_embed_empty_string(self):
        embedder = CodeEmbedder()
        vec = embedder.embed("")
        assert len(vec) == CodeEmbedder.DIM

    def test_embed_batch(self):
        embedder = CodeEmbedder()
        texts = ["hello", "world", "foo bar"]
        vecs = embedder.embed_batch(texts)
        assert len(vecs) == 3
        for v in vecs:
            assert len(v) == CodeEmbedder.DIM

    def test_embed_batch_deterministic(self):
        embedder = CodeEmbedder()
        texts = ["a", "b", "c"]
        assert embedder.embed_batch(texts) == embedder.embed_batch(texts)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestSupportedLanguage:
    def test_python(self):
        assert supported_language_for("foo.py") == "python"

    def test_javascript(self):
        assert supported_language_for("bar.js") == "javascript"

    def test_typescript(self):
        assert supported_language_for("baz.tsx") == "typescript"

    def test_unsupported(self):
        assert supported_language_for("readme.md") is None

    def test_case_insensitive(self):
        assert supported_language_for("main.PY") == "python"


class TestParseFile:
    """These tests exercise tree-sitter parsing.

    They should pass if tree-sitter-languages is installed.
    """

    def test_parse_python_empty(self):
        symbols = parse_file("empty.py", "# just a comment\n")
        names = {s.symbol_name for s in symbols}
        assert "empty.py" in names  # file-level symbol

    def test_parse_python_function(self):
        code = """
def greet(name: str) -> str:
    \"\"\"Return a greeting.\"\"\"
    return f"Hello {name}"
"""
        symbols = parse_file("greet.py", code)
        names = {s.symbol_name for s in symbols}
        assert "greet" in names

    def test_parse_python_class(self):
        code = """
class MyClass:
    \"\"\"My docstring.\"\"\"
    def method(self):
        pass
"""
        symbols = parse_file("myclass.py", code)
        names = {s.symbol_name for s in symbols}
        assert "MyClass" in names

    def test_parse_python_import(self):
        code = "import os\nimport sys\n"
        symbols = parse_file("imports.py", code)
        types = {s.symbol_type for s in symbols}
        names = {s.symbol_name for s in symbols}
        assert "file" in types
        assert any("import" in s.symbol_name for s in symbols)

    def test_parse_javascript_function(self):
        code = """
function hello(name) {
    return "Hello " + name;
}
"""
        symbols = parse_file("hello.js", code)
        names = {s.symbol_name for s in symbols}
        assert "hello" in names

    def test_parse_typescript_class(self):
        code = """
class User {
    name: string;
    greet(): string {
        return `Hi ${this.name}`;
    }
}
"""
        symbols = parse_file("user.ts", code)
        names = {s.symbol_name for s in symbols}
        assert "User" in names

    def test_parse_rust_function(self):
        code = """
fn add(a: i32, b: i32) -> i32 {
    a + b
}
"""
        symbols = parse_file("math.rs", code)
        names = {s.symbol_name for s in symbols}
        assert "add" in names

    def test_parse_go_function(self):
        code = """
package main
func main() {
    println("hello")
}
"""
        symbols = parse_file("main.go", code)
        names = {s.symbol_name for s in symbols}
        assert "main" in names

    def test_parse_java_class(self):
        code = """
public class Hello {
    public static void main(String[] args) {}
}
"""
        symbols = parse_file("Hello.java", code)
        names = {s.symbol_name for s in symbols}
        assert "Hello" in names

    def test_parse_unknown_ext(self):
        symbols = parse_file("data.csv", "a,b,c\n1,2,3\n")
        assert symbols == []

    def test_file_symbol_has_correct_fields(self):
        code = "x = 1\n"
        symbols = parse_file("simple.py", code)
        file_syms = [s for s in symbols if s.symbol_type == "file"]
        assert len(file_syms) == 1
        fs = file_syms[0]
        assert fs.file_path == "simple.py"
        assert fs.language == "python"
        assert fs.start_line == 1


# ---------------------------------------------------------------------------
# Qdrant store tests (in-memory)
# ---------------------------------------------------------------------------


class TestCodeIndexStore:
    def test_create_store_in_memory(self):
        store = create_store(in_memory=True)
        assert isinstance(store, CodeIndexStore)
        store.delete_collection()

    def test_index_and_search(self):
        store = create_store(in_memory=True)
        symbols = [
            CodeSymbol(
                file_path="main.py",
                symbol_name="main",
                symbol_type="function",
                summary="Entry point",
                language="python",
                start_line=1,
                end_line=5,
                code_snippet="def main(): pass",
            ),
            CodeSymbol(
                file_path="utils.py",
                symbol_name="helper",
                symbol_type="function",
                summary="Helper utility",
                language="python",
                start_line=1,
                end_line=3,
                code_snippet="def helper(): return 42",
            ),
        ]
        count = store.index_symbols(symbols)
        assert count == 2

        results = store.search("main function", top_k=5)
        assert len(results) > 0
        assert any(r.symbol_name == "main" for r in results)

        store.delete_collection()

    def test_search_returns_scored_results(self):
        store = create_store(in_memory=True)
        symbols = [
            CodeSymbol(
                file_path="calc.py",
                symbol_name="add",
                symbol_type="function",
                summary="Add two numbers",
                language="python",
                code_snippet="def add(a, b): return a + b",
            ),
        ]
        store.index_symbols(symbols)
        results = store.search("addition", top_k=5)
        assert len(results) > 0
        assert results[0].score > 0
        store.delete_collection()

    def test_search_empty_index(self):
        store = create_store(in_memory=True)
        results = store.search("anything", top_k=5)
        assert results == []
        store.delete_collection()

    def test_filter_by_language(self):
        store = create_store(in_memory=True)
        symbols = [
            CodeSymbol(
                file_path="test.py",
                symbol_name="py_func",
                symbol_type="function",
                summary="Python",
                language="python",
                code_snippet="def py_func(): pass",
            ),
            CodeSymbol(
                file_path="test.js",
                symbol_name="js_func",
                symbol_type="function",
                summary="JS",
                language="javascript",
                code_snippet="function js_func() {}",
            ),
        ]
        store.index_symbols(symbols)
        results = store.search(
            "function", top_k=5, filter_={"language": "python"}
        )
        assert all(r.language == "python" for r in results)

        store.delete_collection()

    def test_symbol_embedding_populated(self):
        store = create_store(in_memory=True)
        sym = CodeSymbol(
            file_path="test.py",
            symbol_name="test",
            symbol_type="function",
            summary="test",
            language="python",
        )
        store.index_symbols([sym])
        assert sym.embedding is not None
        assert len(sym.embedding) == CodeEmbedder.DIM
        store.delete_collection()

    def test_search_result_has_all_fields(self):
        store = create_store(in_memory=True)
        sym = CodeSymbol(
            file_path="app.py",
            symbol_name="run",
            symbol_type="function",
            summary="Run the app",
            language="python",
            start_line=10,
            end_line=20,
            code_snippet="def run(): pass",
        )
        store.index_symbols([sym])
        results = store.search("run", top_k=5)
        assert len(results) > 0
        r = results[0]
        assert r.file_path == "app.py"
        assert r.symbol_name == "run"
        assert r.symbol_type == "function"
        assert r.summary == "Run the app"
        assert r.language == "python"
        assert r.score > 0
        store.delete_collection()


# ---------------------------------------------------------------------------
# Integration: embedder + store + search
# ---------------------------------------------------------------------------


class TestIndexSearchPipeline:
    def test_python_index_search_roundtrip(self):
        store = create_store(in_memory=True)
        code = """
class Calculator:
    \"\"\"A simple calculator.\"\"\"
    def add(self, a, b):
        return a + b
    def subtract(self, a, b):
        return a - b

def version():
    return "1.0"
"""
        symbols = parse_file("calc.py", code)
        assert len(symbols) >= 2  # file + at least one symbol
        store.index_symbols(symbols)

        results = store.search("calculator add", top_k=5)
        names = {r.symbol_name for r in results}
        assert any("Calculator" in n for n in names) or any(
            "calculator" in r.summary.lower() for r in results
        )

        store.delete_collection()

    def test_multi_file_indexing(self):
        store = create_store(in_memory=True)
        files = {
            "auth.py": 'def login(): pass\ndef logout(): pass\n',
            "db.py": 'class Database:\n    def connect(self): pass\n',
        }
        all_symbols = []
        for path, content in files.items():
            all_symbols.extend(parse_file(path, content))
        assert len(all_symbols) >= 4  # 2 files + symbols

        count = store.index_symbols(all_symbols)
        assert count == len(all_symbols)

        results = store.search("database connect", top_k=5)
        assert len(results) > 0

        store.delete_collection()

    def test_search_returns_relevant_first(self):
        store = create_store(in_memory=True)
        symbols = [
            CodeSymbol(
                file_path="math.py",
                symbol_name="add",
                symbol_type="function",
                summary="Addition function",
                language="python",
                code_snippet="def add(a, b): return a + b",
            ),
            CodeSymbol(
                file_path="math.py",
                symbol_name="subtract",
                symbol_type="function",
                summary="Subtraction function",
                language="python",
                code_snippet="def subtract(a, b): return a - b",
            ),
        ]
        store.index_symbols(symbols)
        results = store.search("addition", top_k=5)
        assert len(results) > 0
        # "add" should rank higher than "subtract" for "addition"
        assert results[0].symbol_name in ("add",)
        store.delete_collection()


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestSearchResultSchema:
    def test_search_result_repr(self):
        r = SearchResult(
            file_path="x.py",
            symbol_name="x",
            symbol_type="function",
            summary="",
            language="python",
            code_snippet="",
            score=0.95,
        )
        assert r.file_path == "x.py"
        assert r.score == 0.95


class TestCodeSymbolSchema:
    def test_symbol_defaults(self):
        s = CodeSymbol(
            file_path="f.py",
            symbol_name="f",
            symbol_type="function",
            summary="",
            language="python",
        )
        assert s.start_line == 0
        assert s.end_line == 0
        assert s.code_snippet == ""
        assert s.embedding is None
