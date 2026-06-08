from pydantic import BaseModel
from typing import Optional


class CodeSymbol(BaseModel):
    file_path: str
    symbol_name: str
    symbol_type: str  # "file", "class", "function", "import"
    summary: str
    language: str
    start_line: int = 0
    end_line: int = 0
    code_snippet: str = ""
    embedding: Optional[list[float]] = None


class IndexRequest(BaseModel):
    path: Optional[str] = None
    files: Optional[list[dict]] = None  # [{"path": "...", "content": "..."}]


class IndexResponse(BaseModel):
    indexed_files: int
    indexed_symbols: int
    collection: str


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    filter_: Optional[dict] = None
    collection: str = "code_index"


class SearchResult(BaseModel):
    file_path: str
    symbol_name: str
    symbol_type: str
    summary: str
    language: str
    code_snippet: str
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]
    query: str
    total_hits: int
