import hashlib
import math
from typing import Optional

try:
    from sentence_transformers import SentenceTransformer

    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    _HAS_SENTENCE_TRANSFORMERS = False


class CodeEmbedder:
    """Lightweight code embedder.

    Uses SentenceTransformers ("all-MiniLM-L6-v2") when available,
    falling back to a deterministic character-n-gram hash embedding
    that requires no external ML dependencies.
    """

    DIM = 384

    def __init__(self, model_name: Optional[str] = None):
        self._model = None
        if _HAS_SENTENCE_TRANSFORMERS:
            try:
                self._model = SentenceTransformer(
                    model_name or "all-MiniLM-L6-v2"
                )
            except Exception:
                self._model = None

    def embed(self, text: str) -> list[float]:
        if self._model is not None:
            vec = self._model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        return self._hash_embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._model is not None:
            vecs = self._model.encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vecs]
        return [self._hash_embed(t) for t in texts]

    @staticmethod
    def _hash_embed(text: str, dim: int = DIM) -> list[float]:
        text_lower = text.lower()
        vec = [0.0] * dim

        ngram_sets = [
            _ngrams(text_lower, 2),
            _ngrams(text_lower, 3),
            _ngrams(text_lower, 4),
        ]

        for ngrams in ngram_sets:
            for ng in ngrams:
                h = hashlib.md5(ng.encode("utf-8")).digest()
                idx = int.from_bytes(h[:4], "little") % dim
                sign = 1.0 if (h[4] & 1) == 0 else -1.0
                vec[idx] += sign

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def _ngrams(text: str, n: int) -> set[str]:
    return {text[i : i + n] for i in range(len(text) - n + 1)}
