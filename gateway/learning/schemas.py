from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class LearningType(str, Enum):
    bug_fix = "bug_fix"
    arch_decision = "arch_decision"
    migration_procedure = "migration_procedure"
    deployment_fix = "deployment_fix"


class Learning(BaseModel):
    id: str
    type: LearningType
    title: str
    content: str
    source_issue: str = ""
    resolved_by: str = ""
    tags: list[str] = []
    project: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    version: int = 1


class StoreLearningRequest(BaseModel):
    type: LearningType
    title: str
    content: str
    source_issue: str = ""
    resolved_by: str = ""
    tags: list[str] = []
    project: Optional[str] = None


class StoreLearningResponse(BaseModel):
    id: str
    type: LearningType
    title: str
    version: int
    message: str = "Learning stored"


class UpdateLearningRequest(BaseModel):
    id: str
    type: Optional[LearningType] = None
    title: Optional[str] = None
    content: Optional[str] = None
    source_issue: Optional[str] = None
    resolved_by: Optional[str] = None
    tags: Optional[list[str]] = None
    project: Optional[str] = None


class UpdateLearningResponse(BaseModel):
    id: str
    version: int
    message: str = "Learning updated"


class SearchLearningRequest(BaseModel):
    query: str
    type_filter: Optional[LearningType] = None
    top_k: int = 10
    use_semantic: bool = True


class LearningSearchResult(BaseModel):
    id: str
    type: LearningType
    title: str
    content: str
    source_issue: str
    resolved_by: str
    tags: list[str]
    project: Optional[str] = None
    version: int
    score: float = 0.0


class SearchLearningResponse(BaseModel):
    results: list[LearningSearchResult]
    query: str
    total_hits: int


class LearningResponse(BaseModel):
    id: str
    type: LearningType
    title: str
    content: str
    source_issue: str
    resolved_by: str
    tags: list[str]
    project: Optional[str] = None
    created_at: str
    updated_at: str
    version: int
