from pydantic import BaseModel
from typing import Optional


class MemoryPackFile(BaseModel):
    filename: str
    content: str


class MemoryPack(BaseModel):
    version: str
    created_at: str
    checksum: str
    files: list[MemoryPackFile]


class RebuildRequest(BaseModel):
    path: Optional[str] = None
    files: Optional[list[dict]] = None


class MemoryResponse(BaseModel):
    version: str
    created_at: str
    checksum: str
    file_count: int
    files: dict[str, str]
