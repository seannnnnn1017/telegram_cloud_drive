import asyncio
import io
import json
import os
import secrets
import time
import urllib.parse
import zipfile
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from starlette.requests import Request
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .database import (
    bulk_delete_files,
    create_share,
    delete_share,
    delete_file,
    delete_folders,
    create_folder,
    get_file,
    get_folder,
    get_folder_by_name,
    get_folder_path,
    get_share,
    get_storage_stats,
    init_db,
    insert_file,
    list_files,
    list_files_in_folder_tree,
    list_folder_tree_ids,
    list_folders,
    update_file_name,
)
from .models import (
    BulkDeleteRequest,
    FileResponse,
    FileUpdateRequest,
    FolderCreateRequest,
    FolderEnsureRequest,
    FolderResponse,
    ShareCreateRequest,
    ShareCreateResponse,
    ServerSettingsRequest,
    ServerSettingsResponse,
    ShareSettingsRequest,
    ShareSettingsResponse,
    StorageStats,
    SyncResponse,
    SyncSettingsRequest,
    SyncSettingsResponse,
    TelegramSettingsRequest,
    TelegramSettingsResponse,
)
from .sync import make_caption, start_sync_listener, sync_from_telegram
from .telegram import TelegramClient

load_dotenv()

MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_sse_clients: list[asyncio.Queue] = []


async def _sse_broadcast(data: dict) -> None:
    if not _sse_clients:
        return
    msg = f"event: sync\ndata: {json.dumps(data)}\n\n"
    for q in list(_sse_clients):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass
PROJECT_ROOT = Path(__file__).resolve().parent.parent
folder_ensure_lock = asyncio.Lock()


def zip_entry_name(filename: str, used_names: set[str]) -> str:
    safe_name = (filename or "file").replace("\\", "/").split("/")[-1] or "file"
    if safe_name not in used_names:
        used_names.add(safe_name)
        return safe_name

    stem, dot, suffix = safe_name.rpartition(".")
    if not dot:
        stem, suffix = safe_name, ""
    for index in range(2, 10_000):
        candidate = f"{stem} ({index}){dot}{suffix}" if dot else f"{stem} ({index})"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
    raise ValueError("Too many duplicate filenames")


def get_tg_client() -> TelegramClient:
    client = getattr(app.state, "tg_client", None)
    if client is not None:
        return client
    return build_tg_client()


def env_path() -> Path:
    return Path(os.getenv("TELECLOUD_ENV_FILE", PROJECT_ROOT / ".env")).expanduser()


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    existing = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")


def configured_share_base_url(request: Request) -> str:
    base_url = os.getenv("TELECLOUD_SHARE_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return base_url
    return str(request.base_url).rstrip("/")


def build_tg_client() -> TelegramClient:
    return TelegramClient(
        bot_token=os.environ["BOT_TOKEN"],
        chat_id=os.environ["CHAT_ID"],
        api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
    )


async def replace_tg_client() -> None:
    old_client = getattr(app.state, "tg_client", None)
    old_task = getattr(app.state, "tg_poll_task", None)
    if old_task is not None:
        old_task.cancel()
        with suppress(asyncio.CancelledError):
            await old_task
        app.state.tg_poll_task = None
    if old_client is not None:
        await old_client.aclose()

    app.state.tg_client = build_tg_client()
    if os.getenv("TELEGRAM_INGEST_UPDATES", "true").lower() in {"1", "true", "yes", "on"}:
        app.state.tg_poll_task = asyncio.create_task(poll_telegram_uploads(app.state.tg_client))


def file_response(record) -> FileResponse:
    return FileResponse(
        id=record.id,
        folder_id=record.folder_id,
        name=record.name,
        size=record.size,
        mime_type=record.mime_type,
        has_thumbnail=bool(record.tg_thumb_file_id) and not record.encrypted,
        uploaded_at=record.uploaded_at,
        encrypted=bool(record.encrypted),
    )


async def download_record_response(record, disposition: str = "attachment") -> Response:
    tg = get_tg_client()
    content = await tg.download_file(record.tg_file_id)
    media_type = "application/octet-stream" if record.encrypted else (record.mime_type or "application/octet-stream")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{urllib.parse.quote(record.name)}",
            "X-Vault-Encrypted": "1" if record.encrypted else "0",
        },
    )


def parse_allowed_ingest_chats() -> set[int] | None:
    raw = os.getenv("TELEGRAM_INGEST_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return None
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def extract_message_file(message: dict) -> Optional[dict]:
    if "document" in message:
        doc = message["document"]
        thumbnail = doc.get("thumbnail") or doc.get("thumb") or {}
        return {
            "name": doc.get("file_name") or f"document_{message['message_id']}",
            "size": doc.get("file_size") or 0,
            "mime_type": doc.get("mime_type") or "application/octet-stream",
            "file_id": doc["file_id"],
            "thumb_file_id": thumbnail.get("file_id"),
        }
    if "video" in message:
        video = message["video"]
        thumbnail = video.get("thumbnail") or video.get("thumb") or {}
        return {
            "name": video.get("file_name") or f"video_{message['message_id']}.mp4",
            "size": video.get("file_size") or 0,
            "mime_type": video.get("mime_type") or "video/mp4",
            "file_id": video["file_id"],
            "thumb_file_id": thumbnail.get("file_id"),
        }
    if "photo" in message:
        photos = message["photo"]
        largest = photos[-1]
        thumbnail = photos[-2] if len(photos) > 1 else largest
        return {
            "name": f"photo_{message['message_id']}.jpg",
            "size": largest.get("file_size") or 0,
            "mime_type": "image/jpeg",
            "file_id": largest["file_id"],
            "thumb_file_id": thumbnail.get("file_id"),
        }
    return None


async def ingest_telegram_message(tg: TelegramClient, message: dict) -> bool:
    file_info = extract_message_file(message)
    if file_info is None:
        return False
    channel_message_id = await tg.copy_message(message["chat"]["id"], message["message_id"])
    now = datetime.now(timezone.utc).isoformat()
    await insert_file(
        name=file_info["name"],
        size=file_info["size"],
        mime_type=file_info["mime_type"],
        tg_file_id=file_info["file_id"],
        tg_message_id=channel_message_id,
        uploaded_at=now,
        tg_thumb_file_id=file_info["thumb_file_id"],
        encrypted=False,
    )
    return True



async def poll_telegram_uploads(tg: TelegramClient) -> None:
    allowed_chats = parse_allowed_ingest_chats()
    updates = await tg.get_updates(timeout=0)
    offset = max((u["update_id"] for u in updates), default=-1) + 1

    while True:
        try:
            updates = await tg.get_updates(offset=offset, timeout=25)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                chat_id = message.get("chat", {}).get("id")
                if allowed_chats is not None and chat_id not in allowed_chats:
                    continue
                await ingest_telegram_message(tg, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.tg_client = build_tg_client()
    app.state.tg_poll_task = None
    app.state.tg_sync_task = None
    if os.getenv("TELEGRAM_INGEST_UPDATES", "true").lower() in {"1", "true", "yes", "on"}:
        app.state.tg_poll_task = asyncio.create_task(poll_telegram_uploads(app.state.tg_client))
    if os.getenv("TG_SESSION_STRING", "").strip():
        app.state.tg_sync_task = asyncio.create_task(
            start_sync_listener(on_change=_sse_broadcast)
        )
    try:
        yield
    finally:
        if app.state.tg_poll_task is not None:
            app.state.tg_poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.tg_poll_task
        if app.state.tg_sync_task is not None:
            app.state.tg_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.tg_sync_task
        await app.state.tg_client.aclose()


app = FastAPI(lifespan=lifespan)
app.state.last_activity = time.monotonic()
app.state.idle_timeout_seconds = int(os.getenv("TELECLOUD_IDLE_TIMEOUT", "900") or "900")
app.state.shutdown_requested = False


@app.middleware("http")
async def track_activity(request: Request, call_next):
    app.state.last_activity = time.monotonic()
    return await call_next(request)


@app.get("/api/files", response_model=list[FileResponse])
async def api_list_files(
    q: Optional[str] = Query(None),
    sort: str = Query("date"),
    order: str = Query("desc"),
    type: Optional[str] = Query(None),
    folder_id: Optional[int] = Query(None),
):
    records = await list_files(q=q, sort=sort, order=order, file_type=type, folder_id=folder_id)
    return [file_response(r) for r in records]


@app.get("/api/folders", response_model=list[FolderResponse])
async def api_list_folders(parent_id: Optional[int] = Query(None)):
    folders = await list_folders(parent_id)
    return [FolderResponse(**f.model_dump()) for f in folders]


@app.post("/api/folders", response_model=FolderResponse, status_code=201)
async def api_create_folder(body: FolderCreateRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name is required")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Folder name cannot contain slashes")
    if body.parent_id is not None and await get_folder(body.parent_id) is None:
        raise HTTPException(status_code=404, detail="Parent folder not found")
    now = datetime.now(timezone.utc).isoformat()
    folder_id = await create_folder(name=name, parent_id=body.parent_id, created_at=now)
    folder = await get_folder(folder_id)
    return FolderResponse(**folder.model_dump())


def validate_folder_name(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Folder name is required")
    if "/" in clean or "\\" in clean:
        raise HTTPException(status_code=400, detail="Folder name cannot contain slashes")
    if clean in {".", ".."}:
        raise HTTPException(status_code=400, detail="Folder name is invalid")
    return clean


@app.post("/api/folders/ensure", response_model=FolderResponse, status_code=201)
async def api_ensure_folder(body: FolderEnsureRequest):
    if body.parent_id is not None and await get_folder(body.parent_id) is None:
        raise HTTPException(status_code=404, detail="Parent folder not found")
    if not body.path:
        raise HTTPException(status_code=400, detail="Folder path is required")

    async with folder_ensure_lock:
        parent_id = body.parent_id
        folder = None
        now = datetime.now(timezone.utc).isoformat()
        for part in body.path:
            name = validate_folder_name(part)
            folder = await get_folder_by_name(name, parent_id)
            if folder is None:
                folder_id = await create_folder(name=name, parent_id=parent_id, created_at=now)
                folder = await get_folder(folder_id)
            parent_id = folder.id

    return FolderResponse(**folder.model_dump())


@app.delete("/api/folders/{folder_id}", response_model=dict)
async def api_delete_folder(folder_id: int):
    folder = await get_folder(folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    file_records = await list_files_in_folder_tree(folder_id)
    tg = get_tg_client()
    for record in file_records:
        await tg.delete_message(record.tg_message_id)
    deleted_files = await bulk_delete_files([record.id for record in file_records])
    folder_ids = await list_folder_tree_ids(folder_id)
    deleted_folders = await delete_folders(folder_ids)
    return {"ok": True, "deleted_files": deleted_files, "deleted_folders": deleted_folders}


@app.get("/api/settings/telegram", response_model=TelegramSettingsResponse)
async def api_get_telegram_settings():
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    return TelegramSettingsResponse(
        bot_token_set=bool(bot_token),
        bot_token=bot_token,
        chat_id=os.getenv("CHAT_ID", "").strip(),
        api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org").strip(),
    )


@app.post("/api/settings/telegram/test")
async def api_test_telegram_settings(body: TelegramSettingsRequest):
    client = TelegramClient(
        bot_token=body.bot_token.strip(),
        chat_id=body.chat_id.strip(),
        api_base_url=body.api_base_url.strip() or "https://api.telegram.org",
    )
    try:
        result = await client.test_connection()
        return {"ok": True, "bot": result["bot"].get("username"), "chat": result["chat"].get("title") or result["chat"].get("username") or result["chat"].get("id")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await client.aclose()


@app.put("/api/settings/telegram", response_model=TelegramSettingsResponse)
async def api_update_telegram_settings(body: TelegramSettingsRequest):
    updates = {
        "BOT_TOKEN": body.bot_token.strip(),
        "CHAT_ID": body.chat_id.strip(),
        "TELEGRAM_API_BASE_URL": body.api_base_url.strip() or "https://api.telegram.org",
    }
    if not updates["BOT_TOKEN"]:
        raise HTTPException(status_code=400, detail="Bot token is required")
    if not updates["CHAT_ID"]:
        raise HTTPException(status_code=400, detail="Chat ID is required")

    client = TelegramClient(
        bot_token=updates["BOT_TOKEN"],
        chat_id=updates["CHAT_ID"],
        api_base_url=updates["TELEGRAM_API_BASE_URL"],
    )
    try:
        await client.test_connection()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await client.aclose()

    update_env_file(env_path(), updates)
    os.environ.update(updates)
    await replace_tg_client()
    return TelegramSettingsResponse(
        bot_token_set=True,
        bot_token=updates["BOT_TOKEN"],
        chat_id=updates["CHAT_ID"],
        api_base_url=updates["TELEGRAM_API_BASE_URL"],
    )


@app.get("/api/settings/share", response_model=ShareSettingsResponse)
async def api_get_share_settings():
    return ShareSettingsResponse(base_url=os.getenv("TELECLOUD_SHARE_BASE_URL", "").strip())


@app.put("/api/settings/share", response_model=ShareSettingsResponse)
async def api_update_share_settings(body: ShareSettingsRequest):
    base_url = body.base_url.strip().rstrip("/")
    if base_url and not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Share base URL must start with http:// or https://")
    update_env_file(env_path(), {"TELECLOUD_SHARE_BASE_URL": base_url})
    os.environ["TELECLOUD_SHARE_BASE_URL"] = base_url
    return ShareSettingsResponse(base_url=base_url)


@app.get("/api/settings/server", response_model=ServerSettingsResponse)
async def api_get_server_settings():
    return ServerSettingsResponse(
        idle_timeout_seconds=int(getattr(app.state, "idle_timeout_seconds", 900))
    )


@app.put("/api/settings/server", response_model=ServerSettingsResponse)
async def api_update_server_settings(body: ServerSettingsRequest):
    seconds = body.idle_timeout_seconds
    if seconds < 0:
        raise HTTPException(status_code=400, detail="Idle timeout cannot be negative")
    app.state.idle_timeout_seconds = seconds
    os.environ["TELECLOUD_IDLE_TIMEOUT"] = str(seconds)
    update_env_file(env_path(), {"TELECLOUD_IDLE_TIMEOUT": str(seconds)})
    return ServerSettingsResponse(idle_timeout_seconds=seconds)


@app.post("/api/server/shutdown", response_model=dict)
async def api_shutdown_server():
    app.state.shutdown_requested = True
    return {"ok": True}


@app.post("/api/upload", response_model=FileResponse, status_code=201)
async def api_upload(
    file: UploadFile = File(...),
    folder_id: Optional[int] = Form(None),
    encrypted: bool = Form(False),
    original_mime_type: Optional[str] = Form(None),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if folder_id is not None and await get_folder(folder_id) is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File size {len(content) // 1024 // 1024} MB exceeds the 20 MB limit.",
        )
    tg = get_tg_client()
    stored_mime_type = original_mime_type if encrypted and original_mime_type else (file.content_type or "application/octet-stream")
    now = datetime.now(timezone.utc).isoformat()
    file_uid = secrets.token_hex(8)
    # Build full path for caption so sync can reconstruct folder hierarchy
    caption_name = file.filename
    if folder_id is not None:
        parts = await get_folder_path(folder_id)
        if parts:
            caption_name = "/".join(parts) + "/" + file.filename
    # Embed metadata as caption so any device can sync from Telegram history
    caption = make_caption(
        name=caption_name,
        size=len(content),
        mime_type=stored_mime_type,
        encrypted=encrypted,
        uploaded_at=now,
        uid=file_uid,
    )
    tg_result = await tg.send_document(
        file.filename, content, file.content_type or "application/octet-stream",
        caption=caption,
    )
    thumb_file_id = None if encrypted else tg_result.get("thumb_file_id")
    new_id = await insert_file(
        name=file.filename,
        size=len(content),
        mime_type=stored_mime_type,
        tg_file_id=tg_result["file_id"],
        tg_thumb_file_id=thumb_file_id,
        tg_message_id=tg_result["message_id"],
        uploaded_at=now,
        folder_id=folder_id,
        encrypted=encrypted,
        uid=file_uid,
    )
    record = await get_file(new_id)
    return file_response(record)


@app.get("/api/files/{file_id}/download")
async def api_download(file_id: int):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    return await download_record_response(record)


@app.get("/api/files/{file_id}/preview")
async def api_preview(file_id: int):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    if record.encrypted:
        raise HTTPException(status_code=415, detail="Encrypted files must be previewed locally")
    mime = record.mime_type or ""
    if not (mime.startswith("image/") or mime.startswith("video/") or mime == "application/pdf"):
        raise HTTPException(status_code=415, detail="Preview not supported for this file type")
    tg = get_tg_client()
    content = await tg.download_file(record.tg_file_id)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{urllib.parse.quote(record.name)}"},
    )


@app.get("/api/files/{file_id}/thumbnail")
async def api_thumbnail(file_id: int):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    if record.encrypted:
        raise HTTPException(status_code=404, detail="Thumbnail not available for encrypted files")
    mime = record.mime_type or ""
    tg = get_tg_client()
    if record.tg_thumb_file_id:
        content = await tg.download_file(record.tg_thumb_file_id)
        return Response(
            content=content,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    if mime.startswith("image/"):
        content = await tg.download_file(record.tg_file_id)
        return Response(
            content=content,
            media_type=mime,
            headers={"Cache-Control": "private, max-age=3600"},
        )
    raise HTTPException(status_code=404, detail="Thumbnail not available")


@app.patch("/api/files/{file_id}", response_model=FileResponse)
async def api_update_file(file_id: int, body: FileUpdateRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Filename is required")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Filename cannot contain slashes")
    record = await update_file_name(file_id, name)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    return file_response(record)


@app.delete("/api/files/{file_id}", response_model=dict)
async def api_delete(file_id: int):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    tg = get_tg_client()
    await tg.delete_message(record.tg_message_id)
    await delete_file(file_id)
    return {"ok": True}


@app.post("/api/files/bulk-delete", response_model=dict)
async def api_bulk_delete(body: BulkDeleteRequest):
    if not body.ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    tg = get_tg_client()
    for fid in body.ids:
        record = await get_file(fid)
        if record:
            await tg.delete_message(record.tg_message_id)
    deleted = await bulk_delete_files(body.ids)
    return {"deleted": deleted}


@app.post("/api/files/bulk-download")
async def api_bulk_download(body: BulkDeleteRequest):
    if not body.ids:
        raise HTTPException(status_code=400, detail="No IDs provided")

    tg = get_tg_client()
    archive = io.BytesIO()
    used_names: set[str] = set()
    found = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fid in body.ids:
            record = await get_file(fid)
            if record is None:
                continue
            content = await tg.download_file(record.tg_file_id)
            zf.writestr(zip_entry_name(record.name, used_names), content)
            found += 1

    if found == 0:
        raise HTTPException(status_code=404, detail="No files found")

    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=vault-selection.zip"},
    )


@app.post("/api/files/{file_id}/share", response_model=ShareCreateResponse)
async def api_create_file_share(file_id: int, body: ShareCreateRequest, request: Request):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    token = secrets.token_urlsafe(24)
    expires_at = None
    if body.expires_in_seconds is not None:
        if body.expires_in_seconds <= 0:
            raise HTTPException(status_code=400, detail="Expiry must be positive")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=body.expires_in_seconds)
    await create_share(token, file_id, expires_at.isoformat() if expires_at else None)
    base_url = configured_share_base_url(request)
    return ShareCreateResponse(
        token=token,
        url=f"{base_url}/api/share/{token}",
        expires_at=expires_at,
    )


@app.get("/api/share/{token}", name="api_download_share")
async def api_download_share(token: str):
    share = await get_share(token)
    if share is None:
        raise HTTPException(status_code=404, detail="Share link not found")
    if share["expires_at"]:
        expires_at = datetime.fromisoformat(share["expires_at"])
        if expires_at <= datetime.now(timezone.utc):
            await delete_share(token)
            raise HTTPException(status_code=410, detail="Share link expired")
    record = await get_file(share["file_id"])
    if record is None:
        await delete_share(token)
        raise HTTPException(status_code=404, detail="File not found")
    return await download_record_response(record)


@app.get("/api/storage", response_model=StorageStats)
async def api_storage():
    stats = await get_storage_stats()
    return StorageStats(**stats)


@app.get("/api/settings/sync", response_model=SyncSettingsResponse)
async def api_get_sync_settings():
    api_id = os.getenv("TG_API_ID", "").strip()
    return SyncSettingsResponse(
        api_id=api_id,
        api_id_set=bool(api_id),
        has_session=bool(os.getenv("TG_SESSION_STRING", "").strip()),
    )


@app.put("/api/settings/sync", response_model=SyncSettingsResponse)
async def api_update_sync_settings(body: SyncSettingsRequest):
    api_id = body.api_id.strip()
    api_hash = body.api_hash.strip()
    if not api_id or not api_hash:
        raise HTTPException(status_code=400, detail="API ID 和 API Hash 均為必填")
    try:
        int(api_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="API ID 必須為數字")
    update_env_file(env_path(), {"TG_API_ID": api_id, "TG_API_HASH": api_hash})
    os.environ["TG_API_ID"] = api_id
    os.environ["TG_API_HASH"] = api_hash
    return SyncSettingsResponse(
        api_id=api_id,
        api_id_set=True,
        has_session=bool(os.getenv("TG_SESSION_STRING", "").strip()),
    )


@app.get("/api/events")
async def api_events(request: Request):
    """Server-Sent Events stream. Pushes a 'sync' event when the local DB changes."""
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    _sse_clients.append(q)

    async def generate():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in _sse_clients:
                _sse_clients.remove(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sync", response_model=SyncResponse)
async def api_sync():
    try:
        result = await sync_from_telegram()
        if result.get("imported", 0) > 0 or result.get("deleted", 0) > 0:
            asyncio.create_task(_sse_broadcast(result))
        return SyncResponse(**result)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"同步失敗：{exc}") from exc


app.mount("/", StaticFiles(directory=PROJECT_ROOT / "frontend", html=True), name="static")
