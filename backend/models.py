from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FileRecord(BaseModel):
    id: int
    name: str
    size: int
    mime_type: Optional[str] = None
    tg_file_id: str
    tg_message_id: int
    uploaded_at: datetime


class FileResponse(BaseModel):
    id: int
    name: str
    size: int
    mime_type: Optional[str] = None
    uploaded_at: datetime


class StorageStats(BaseModel):
    used_bytes: int
    file_count: int


class BulkDeleteRequest(BaseModel):
    ids: list[int]
