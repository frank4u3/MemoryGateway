from pydantic import BaseModel
from typing import Optional


class SemanticCacheEntry(BaseModel):
    canonical_hash: str
    canonical_text: str
    model: str
    response: dict
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None


class SemanticSearchResult(BaseModel):
    canonical_hash: str
    response: dict
    score: float
