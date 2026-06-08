from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ContextType(str, Enum):
    architecture = "architecture"
    coding_standards = "coding_standards"
    roadmap = "roadmap"
    active_state = "active_state"
    custom = "custom"


class ContextBlock(BaseModel):
    id: str
    type: ContextType
    title: str
    content: str
    tags: list[str] = []
    source: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    version: int = 1


class RegisterContextRequest(BaseModel):
    type: ContextType
    title: str
    content: str
    tags: list[str] = []
    source: Optional[str] = None


class RegisterContextResponse(BaseModel):
    id: str
    type: ContextType
    title: str
    version: int
    message: str = "Context block registered"


class UpdateContextRequest(BaseModel):
    id: str
    type: Optional[ContextType] = None
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None


class UpdateContextResponse(BaseModel):
    id: str
    version: int
    message: str = "Context block updated"


class SearchContextRequest(BaseModel):
    query: str
    type_filter: Optional[ContextType] = None
    top_k: int = 10


class ContextSearchResult(BaseModel):
    id: str
    type: ContextType
    title: str
    content: str
    tags: list[str]
    source: Optional[str] = None
    version: int
    score: float = 0.0


class SearchContextResponse(BaseModel):
    results: list[ContextSearchResult]
    query: str
    total_hits: int


class ContextResponse(BaseModel):
    id: str
    type: ContextType
    title: str
    content: str
    tags: list[str]
    source: Optional[str] = None
    created_at: str
    updated_at: str
    version: int
