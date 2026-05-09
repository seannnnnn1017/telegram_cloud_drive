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
    encrypted: bool = False


class FileResponse(BaseModel):
    id: int
    folder_id: Optional[int] = None
    name: str
    size: int
    mime_type: Optional[str] = None
    has_thumbnail: bool = False
    uploaded_at: datetime
    encrypted: bool = False


class FileUpdateRequest(BaseModel):
    name: str


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


class FolderEnsureRequest(BaseModel):
    path: list[str]
    parent_id: Optional[int] = None


class TelegramSettingsRequest(BaseModel):
    bot_token: str
    chat_id: str
    api_base_url: str = "https://api.telegram.org"


class TelegramSettingsResponse(BaseModel):
    bot_token_set: bool
    bot_token: str = ""
    chat_id: str = ""
    api_base_url: str = "https://api.telegram.org"


class ShareSettingsRequest(BaseModel):
    base_url: str = ""


class ShareSettingsResponse(BaseModel):
    base_url: str = ""


class ServerSettingsRequest(BaseModel):
    idle_timeout_seconds: int


class ServerSettingsResponse(BaseModel):
    idle_timeout_seconds: int


class ShareCreateRequest(BaseModel):
    expires_in_seconds: Optional[int] = None


class ShareCreateResponse(BaseModel):
    token: str
    url: str
    expires_at: Optional[datetime] = None
