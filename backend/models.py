from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FileRecord(BaseModel):
    id: int
    folder_id: Optional[int] = None
    name: str
    size: int
    mime_type: Optional[str] = None
    tg_file_id: str
    tg_thumb_file_id: Optional[str] = None
    tg_message_id: int
    uploaded_at: datetime


class FileResponse(BaseModel):
    id: int
    folder_id: Optional[int] = None
    name: str
    size: int
    mime_type: Optional[str] = None
    has_thumbnail: bool = False
    uploaded_at: datetime


class StorageStats(BaseModel):
    used_bytes: int
    file_count: int


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class FolderRecord(BaseModel):
    id: int
    parent_id: Optional[int] = None
    name: str
    created_at: datetime


class FolderResponse(BaseModel):
    id: int
    parent_id: Optional[int] = None
    name: str
    created_at: datetime


class FolderCreateRequest(BaseModel):
    name: str
    parent_id: Optional[int] = None
