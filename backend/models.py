from pydantic import BaseModel
from typing import Optional


class FileRecord(BaseModel):
    id: int
    name: str
    size: int
    mime_type: Optional[str]
    tg_file_id: str
    tg_message_id: int
    uploaded_at: str


class FileResponse(BaseModel):
    id: int
    name: str
    size: int
    mime_type: Optional[str]
    uploaded_at: str


class StorageStats(BaseModel):
    used_bytes: int
    file_count: int


class BulkDeleteRequest(BaseModel):
    ids: list[int]
