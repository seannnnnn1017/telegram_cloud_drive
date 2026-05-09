import asyncio
import io
import os
import urllib.parse
import zipfile
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .database import (
    bulk_delete_files,
    delete_file,
    delete_folders,
    create_folder,
    get_file,
    get_folder,
    get_storage_stats,
    init_db,
    insert_file,
    list_files,
    list_files_in_folder_tree,
    list_folder_tree_ids,
    list_folders,
)
from .models import BulkDeleteRequest, FileResponse, FolderCreateRequest, FolderResponse, StorageStats
from .telegram import TelegramClient

load_dotenv()

MAX_BYTES = 20 * 1024 * 1024  # 20 MB


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


def build_tg_client() -> TelegramClient:
    return TelegramClient(
        bot_token=os.environ["BOT_TOKEN"],
        chat_id=os.environ["CHAT_ID"],
        api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
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
    if os.getenv("TELEGRAM_INGEST_UPDATES", "true").lower() in {"1", "true", "yes", "on"}:
        app.state.tg_poll_task = asyncio.create_task(poll_telegram_uploads(app.state.tg_client))
    try:
        yield
    finally:
        if app.state.tg_poll_task is not None:
            app.state.tg_poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.tg_poll_task
        await app.state.tg_client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/api/files", response_model=list[FileResponse])
async def api_list_files(
    q: Optional[str] = Query(None),
    sort: str = Query("date"),
    order: str = Query("desc"),
    type: Optional[str] = Query(None),
    folder_id: Optional[int] = Query(None),
):
    records = await list_files(q=q, sort=sort, order=order, file_type=type, folder_id=folder_id)
    return [
        FileResponse(
            id=r.id, folder_id=r.folder_id, name=r.name, size=r.size,
            mime_type=r.mime_type, has_thumbnail=bool(r.tg_thumb_file_id),
            uploaded_at=r.uploaded_at,
        )
        for r in records
    ]


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


@app.post("/api/upload", response_model=FileResponse, status_code=201)
async def api_upload(file: UploadFile = File(...), folder_id: Optional[int] = Form(None)):
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
    tg_result = await tg.send_document(
        file.filename, content, file.content_type or "application/octet-stream"
    )
    now = datetime.now(timezone.utc).isoformat()
    new_id = await insert_file(
        name=file.filename,
        size=len(content),
        mime_type=file.content_type,
        tg_file_id=tg_result["file_id"],
        tg_thumb_file_id=tg_result.get("thumb_file_id"),
        tg_message_id=tg_result["message_id"],
        uploaded_at=now,
        folder_id=folder_id,
    )
    record = await get_file(new_id)
    return FileResponse(
        id=record.id, folder_id=record.folder_id, name=record.name, size=record.size,
        mime_type=record.mime_type, has_thumbnail=bool(record.tg_thumb_file_id),
        uploaded_at=record.uploaded_at,
    )


@app.get("/api/files/{file_id}/download")
async def api_download(file_id: int):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    tg = get_tg_client()
    content = await tg.download_file(record.tg_file_id)
    return Response(
        content=content,
        media_type=record.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(record.name)}"},
    )


@app.get("/api/files/{file_id}/preview")
async def api_preview(file_id: int):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
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


@app.get("/api/storage", response_model=StorageStats)
async def api_storage():
    stats = await get_storage_stats()
    return StorageStats(**stats)


app.mount("/", StaticFiles(directory="frontend", html=True), name="static")
