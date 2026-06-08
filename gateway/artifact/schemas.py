from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ArtifactType(str, Enum):
    generated_code = "generated_code"
    api = "api"
    prompt = "prompt"
    workflow = "workflow"
    schema = "schema"
    architecture_decision = "architecture_decision"


class Artifact(BaseModel):
    id: str
    type: ArtifactType
    title: str
    content: str
    creator_agent: str
    git_commit: Optional[str] = None
    tags: list[str] = []
    project: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    version: int = 1


class StoreArtifactRequest(BaseModel):
    type: ArtifactType
    title: str
    content: str
    creator_agent: str
    git_commit: Optional[str] = None
    tags: list[str] = []
    project: Optional[str] = None


class StoreArtifactResponse(BaseModel):
    id: str
    type: ArtifactType
    title: str
    version: int
    message: str = "Artifact stored"


class UpdateArtifactRequest(BaseModel):
    id: str
    type: Optional[ArtifactType] = None
    title: Optional[str] = None
    content: Optional[str] = None
    git_commit: Optional[str] = None
    tags: Optional[list[str]] = None
    project: Optional[str] = None


class UpdateArtifactResponse(BaseModel):
    id: str
    version: int
    message: str = "Artifact updated"


class SearchArtifactRequest(BaseModel):
    query: str
    type_filter: Optional[ArtifactType] = None
    top_k: int = 10
    use_semantic: bool = True


class ArtifactSearchResult(BaseModel):
    id: str
    type: ArtifactType
    title: str
    content: str
    creator_agent: str
    git_commit: Optional[str] = None
    tags: list[str]
    project: Optional[str] = None
    version: int
    score: float = 0.0


class SearchArtifactResponse(BaseModel):
    results: list[ArtifactSearchResult]
    query: str
    total_hits: int


class ArtifactResponse(BaseModel):
    id: str
    type: ArtifactType
    title: str
    content: str
    creator_agent: str
    git_commit: Optional[str] = None
    tags: list[str]
    project: Optional[str] = None
    created_at: str
    updated_at: str
    version: int
