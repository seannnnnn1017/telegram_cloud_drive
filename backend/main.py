import os
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .database import (
    bulk_delete_files,
    delete_file,
    get_file,
    get_storage_stats,
    init_db,
    insert_file,
    list_files,
)
from .models import BulkDeleteRequest, FileResponse, StorageStats
from .telegram import TelegramClient

load_dotenv()

MAX_BYTES = 20 * 1024 * 1024  # 20 MB


def get_tg_client() -> TelegramClient:
    return TelegramClient(
        bot_token=os.environ["BOT_TOKEN"],
        chat_id=os.environ["CHAT_ID"],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/api/files", response_model=list[FileResponse])
async def api_list_files(
    q: Optional[str] = Query(None),
    sort: str = Query("date"),
    order: str = Query("desc"),
    type: Optional[str] = Query(None),
):
    records = await list_files(q=q, sort=sort, order=order, file_type=type)
    return [
        FileResponse(
            id=r.id, name=r.name, size=r.size,
            mime_type=r.mime_type, uploaded_at=r.uploaded_at,
        )
        for r in records
    ]


@app.post("/api/upload", response_model=FileResponse, status_code=201)
async def api_upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
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
        tg_message_id=tg_result["message_id"],
        uploaded_at=now,
    )
    record = await get_file(new_id)
    return FileResponse(
        id=record.id, name=record.name, size=record.size,
        mime_type=record.mime_type, uploaded_at=record.uploaded_at,
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
    if not (mime.startswith("image/") or mime == "application/pdf"):
        raise HTTPException(status_code=415, detail="Preview not supported for this file type")
    tg = get_tg_client()
    content = await tg.download_file(record.tg_file_id)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{urllib.parse.quote(record.name)}"},
    )


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


@app.get("/api/storage", response_model=StorageStats)
async def api_storage():
    stats = await get_storage_stats()
    return StorageStats(**stats)


app.mount("/", StaticFiles(directory="frontend", html=True), name="static")
