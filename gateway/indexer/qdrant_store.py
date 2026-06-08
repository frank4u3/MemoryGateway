import uuid
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
)

from gateway.indexer.schemas import CodeSymbol, SearchResult
from gateway.indexer.embedder import CodeEmbedder

_COLLECTION_DEFAULT = "code_index"
_VECTOR_SIZE = CodeEmbedder.DIM


class CodeIndexStore:
    """Thin wrapper around Qdrant for code symbol storage and search."""

    def __init__(
        self,
        location: Optional[str] = None,
        url: Optional[str] = None,
        port: int = 6333,
        prefer_grpc: bool = False,
        embedder: Optional[CodeEmbedder] = None,
    ):
        self._embedder = embedder or CodeEmbedder()
        self._client = QdrantClient(
            location=location,
            url=url,
            port=port,
            prefer_grpc=prefer_grpc,
        )

    def ensure_collection(
        self, collection: str = _COLLECTION_DEFAULT
    ):
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(
                    size=_VECTOR_SIZE,
                    distance=Distance.COSINE,
                ),
            )

    def index_symbols(
        self,
        symbols: list[CodeSymbol],
        collection: str = _COLLECTION_DEFAULT,
    ) -> int:
        if not symbols:
            return 0

        self.ensure_collection(collection)

        texts = [_build_embedding_text(s) for s in symbols]
        embeddings = self._embedder.embed_batch(texts)

        points = []
        for sym, emb in zip(symbols, embeddings):
            sym.embedding = emb
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=emb,
                    payload={
                        "file_path": sym.file_path,
                        "symbol_name": sym.symbol_name,
                        "symbol_type": sym.symbol_type,
                        "summary": sym.summary,
                        "language": sym.language,
                        "start_line": sym.start_line,
                        "end_line": sym.end_line,
                        "code_snippet": sym.code_snippet,
                    },
                )
            )

        self._client.upsert(
            collection_name=collection,
            points=points,
        )
        return len(points)

    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_: Optional[dict] = None,
        collection: str = _COLLECTION_DEFAULT,
    ) -> list[SearchResult]:
        self.ensure_collection(collection)

        query_vec = self._embedder.embed(query)

        qfilter = None
        if filter_:
            conditions = []
            for key, value in filter_.items():
                conditions.append(
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=value),
                    )
                )
            if conditions:
                qfilter = Filter(must=conditions)

        response = self._client.query_points(
            collection_name=collection,
            query=query_vec,
            query_filter=qfilter,
            limit=top_k,
            with_payload=True,
        )

        return [
            SearchResult(
                file_path=point.payload.get("file_path", ""),
                symbol_name=point.payload.get("symbol_name", ""),
                symbol_type=point.payload.get("symbol_type", ""),
                summary=point.payload.get("summary", ""),
                language=point.payload.get("language", ""),
                code_snippet=point.payload.get("code_snippet", ""),
                score=point.score,
            )
            for point in response.points
        ]

    def delete_collection(
        self, collection: str = _COLLECTION_DEFAULT
    ):
        try:
            self._client.delete_collection(collection)
        except Exception:
            pass


def _build_embedding_text(sym: CodeSymbol) -> str:
    parts = [
        f"symbol: {sym.symbol_name}",
        f"type: {sym.symbol_type}",
        f"file: {sym.file_path}",
    ]
    if sym.summary:
        parts.append(f"summary: {sym.summary}")
    if sym.code_snippet:
        parts.append(f"code: {sym.code_snippet}")
    return "\n".join(parts)


def create_store(
    in_memory: bool = True,
    embedder: Optional[CodeEmbedder] = None,
) -> CodeIndexStore:
    if in_memory:
        store = CodeIndexStore(
            location=":memory:", embedder=embedder
        )
    else:
        store = CodeIndexStore(
            url="localhost", port=6333, embedder=embedder
        )
    store.ensure_collection()
    return store
