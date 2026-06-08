from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class MemoryScope(str, Enum):
    shared = "shared"
    project = "project"
    agent = "agent"


class MemoryPermission(str, Enum):
    read = "read"
    write = "write"
    admin = "admin"


class SourceType(str, Enum):
    context = "context"
    artifact = "artifact"
    indexer = "indexer"
    memory_pack = "memory_pack"
    inline = "inline"


class MemoryRecord(BaseModel):
    id: str
    title: str
    summary: str
    scope: MemoryScope
    scope_value: str = ""
    permissions: dict[str, MemoryPermission] = {}
    source_type: SourceType
    source_id: Optional[str] = None
    source_project: Optional[str] = None
    source_agent: Optional[str] = None
    tags: list[str] = []
    creator_agent: str = ""
    created_at: str = ""
    updated_at: str = ""


class ShareMemoryRequest(BaseModel):
    source_type: SourceType
    source_id: str
    scope: MemoryScope
    scope_value: str = ""
    permissions: Optional[dict[str, MemoryPermission]] = None


class ShareMemoryResponse(BaseModel):
    id: str
    source_type: SourceType
    source_id: str
    scope: MemoryScope
    message: str = "Memory shared"


class CreateMemoryRequest(BaseModel):
    title: str
    summary: str
    content: str
    scope: MemoryScope
    scope_value: str = ""
    permissions: Optional[dict[str, MemoryPermission]] = None
    tags: list[str] = []
    creator_agent: str
    project: Optional[str] = None


class CreateMemoryResponse(BaseModel):
    id: str
    title: str
    scope: MemoryScope
    message: str = "Memory created"


class SearchMemoryRequest(BaseModel):
    query: str
    agent_id: str
    scope_filter: Optional[MemoryScope] = None
    source_filter: Optional[SourceType] = None
    project_filter: Optional[str] = None
    top_k: int = 20


class MemorySearchResult(BaseModel):
    id: str
    title: str
    summary: str
    scope: MemoryScope
    scope_value: str = ""
    source_type: SourceType
    source_id: Optional[str] = None
    source_project: Optional[str] = None
    source_agent: Optional[str] = None
    tags: list[str] = []
    creator_agent: str = ""
    created_at: str = ""
    updated_at: str = ""
    score: float = 0.0


class SearchMemoryResponse(BaseModel):
    results: list[MemorySearchResult]
    query: str
    agent_id: str
    total_hits: int


class AgentContextResponse(BaseModel):
    agent_id: str
    memories: list[MemorySearchResult]
    total_count: int


class UpdatePermissionsRequest(BaseModel):
    record_id: str
    permissions: dict[str, MemoryPermission]
    agent_id: str


class UpdatePermissionsResponse(BaseModel):
    id: str
    message: str = "Permissions updated"
