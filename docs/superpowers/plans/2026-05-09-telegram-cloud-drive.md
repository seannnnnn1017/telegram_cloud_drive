# Telegram Cloud Drive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LAN-hosted personal cloud drive web app that uses a Telegram bot channel as the file storage backend, with a dark/neon-green "Vault" UI.

**Architecture:** FastAPI backend proxies all file I/O through the Telegram Bot API; SQLite stores file metadata (name, size, mime, Telegram file_id/message_id, upload date); a single `frontend/index.html` serves the entire UI and is served as a static file by FastAPI.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, aiosqlite, httpx, python-multipart, python-dotenv; tests with pytest + pytest-asyncio + pytest-httpx.

---

## File Map

```
telegram_cloud_drive/
├── backend/
│   ├── __init__.py       empty package marker
│   ├── models.py         Pydantic request/response models
│   ├── database.py       SQLite init + all async CRUD helpers
│   ├── telegram.py       Telegram Bot API wrapper (httpx)
│   └── main.py           FastAPI app, all routes, static mount
├── frontend/
│   └── index.html        Single-page app — Vault dark/green design
├── tests/
│   ├── __init__.py
│   ├── test_database.py
│   ├── test_telegram.py
│   └── test_api.py
├── pytest.ini
├── requirements.txt
├── .env.example
└── README.md
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `.env.example`
- Create: `backend/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p backend frontend tests
```

- [ ] **Step 2: Write `requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
httpx==0.27.2
aiosqlite==0.20.0
python-multipart==0.0.9
python-dotenv==1.0.1
pydantic==2.8.2
pytest==8.3.3
pytest-asyncio==0.24.0
pytest-httpx==0.32.0
```

- [ ] **Step 3: Write `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 4: Write `.env.example`**

```
BOT_TOKEN=your_bot_token_here
CHAT_ID=-100your_channel_id_here
VAULT_DB_PATH=vault.db
```

- [ ] **Step 5: Create empty package markers**

Create `backend/__init__.py` and `tests/__init__.py` — both empty files.

- [ ] **Step 6: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 7: Commit**

```bash
git init
git add requirements.txt pytest.ini .env.example backend/__init__.py tests/__init__.py
git commit -m "chore: project scaffold"
```

---

## Task 2: Data Models

**Files:**
- Create: `backend/models.py`

- [ ] **Step 1: Write `backend/models.py`**

```python
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
```

- [ ] **Step 2: Verify import**

```bash
python -c "from backend.models import FileRecord, FileResponse, StorageStats, BulkDeleteRequest; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat: add Pydantic data models"
```

---

## Task 3: Database Layer

**Files:**
- Create: `backend/database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write failing tests in `tests/test_database.py`**

```python
import pytest
import backend.database as db_module
from pathlib import Path

# Redirect to temp DB before any import side-effects
db_module.DB_PATH = Path("/tmp/test_vault.db")

from backend.database import (
    init_db, insert_file, get_file, list_files,
    delete_file, bulk_delete_files, get_storage_stats,
)


@pytest.fixture(autouse=True)
async def clean_db():
    db_module.DB_PATH.unlink(missing_ok=True)
    await init_db()
    yield
    db_module.DB_PATH.unlink(missing_ok=True)


async def test_insert_and_get_file():
    fid = await insert_file("photo.jpg", 2048, "image/jpeg", "tg_abc", 10, "2026-01-01T00:00:00+00:00")
    record = await get_file(fid)
    assert record.name == "photo.jpg"
    assert record.size == 2048
    assert record.mime_type == "image/jpeg"
    assert record.tg_file_id == "tg_abc"
    assert record.tg_message_id == 10


async def test_get_file_not_found():
    record = await get_file(9999)
    assert record is None


async def test_list_files_all():
    await insert_file("a.jpg", 100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    await insert_file("b.pdf", 200, "application/pdf", "f2", 2, "2026-01-02T00:00:00+00:00")
    files = await list_files()
    assert len(files) == 2


async def test_list_files_search():
    await insert_file("vacation.jpg", 100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    await insert_file("report.pdf", 200, "application/pdf", "f2", 2, "2026-01-02T00:00:00+00:00")
    results = await list_files(q="vacation")
    assert len(results) == 1
    assert results[0].name == "vacation.jpg"


async def test_list_files_by_type():
    await insert_file("a.jpg", 100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    await insert_file("b.mp4", 500, "video/mp4", "f2", 2, "2026-01-02T00:00:00+00:00")
    await insert_file("c.pdf", 200, "application/pdf", "f3", 3, "2026-01-03T00:00:00+00:00")
    images = await list_files(file_type="image")
    assert len(images) == 1
    assert images[0].name == "a.jpg"
    videos = await list_files(file_type="video")
    assert len(videos) == 1
    assert videos[0].name == "b.mp4"


async def test_list_files_sort_by_size():
    await insert_file("small.jpg", 100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    await insert_file("big.jpg", 900, "image/jpeg", "f2", 2, "2026-01-02T00:00:00+00:00")
    results = await list_files(sort="size", order="asc")
    assert results[0].name == "small.jpg"
    assert results[1].name == "big.jpg"


async def test_delete_file():
    fid = await insert_file("del.txt", 10, "text/plain", "f1", 1, "2026-01-01T00:00:00+00:00")
    removed = await delete_file(fid)
    assert removed is True
    assert await get_file(fid) is None


async def test_bulk_delete():
    id1 = await insert_file("x.jpg", 10, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    id2 = await insert_file("y.jpg", 20, "image/jpeg", "f2", 2, "2026-01-02T00:00:00+00:00")
    id3 = await insert_file("z.jpg", 30, "image/jpeg", "f3", 3, "2026-01-03T00:00:00+00:00")
    count = await bulk_delete_files([id1, id2])
    assert count == 2
    remaining = await list_files()
    assert len(remaining) == 1
    assert remaining[0].id == id3


async def test_storage_stats():
    await insert_file("a.jpg", 1000, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    await insert_file("b.pdf", 2000, "application/pdf", "f2", 2, "2026-01-02T00:00:00+00:00")
    stats = await get_storage_stats()
    assert stats["used_bytes"] == 3000
    assert stats["file_count"] == 2
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_database.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.database'`

- [ ] **Step 3: Write `backend/database.py`**

```python
import os
import aiosqlite
from pathlib import Path
from typing import Optional
from .models import FileRecord

DB_PATH = Path(os.getenv("VAULT_DB_PATH", "vault.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    size           INTEGER NOT NULL,
    mime_type      TEXT,
    tg_file_id     TEXT    NOT NULL,
    tg_message_id  INTEGER NOT NULL,
    uploaded_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_name ON files(name);
CREATE INDEX IF NOT EXISTS idx_date ON files(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_mime ON files(mime_type);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def insert_file(
    name: str,
    size: int,
    mime_type: Optional[str],
    tg_file_id: str,
    tg_message_id: int,
    uploaded_at: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO files (name, size, mime_type, tg_file_id, tg_message_id, uploaded_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, size, mime_type, tg_file_id, tg_message_id, uploaded_at),
        )
        await db.commit()
        return cur.lastrowid


async def get_file(file_id: int) -> Optional[FileRecord]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as cur:
            row = await cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def list_files(
    q: Optional[str] = None,
    sort: str = "date",
    order: str = "desc",
    file_type: Optional[str] = None,
) -> list[FileRecord]:
    col_map = {"name": "name", "date": "uploaded_at", "size": "size", "type": "mime_type"}
    sort_col = col_map.get(sort, "uploaded_at")
    direction = "DESC" if order == "desc" else "ASC"
    type_map = {"image": "image/%", "video": "video/%", "document": "application/%"}

    conds, params = [], []
    if q:
        conds.append("name LIKE ?")
        params.append(f"%{q}%")
    if file_type and file_type in type_map:
        conds.append("mime_type LIKE ?")
        params.append(type_map[file_type])

    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    sql = f"SELECT * FROM files {where} ORDER BY {sort_col} {direction}"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [FileRecord(**dict(r)) for r in await cur.fetchall()]


async def delete_file(file_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()
        return cur.rowcount > 0


async def bulk_delete_files(ids: list[int]) -> int:
    placeholders = ",".join("?" * len(ids))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"DELETE FROM files WHERE id IN ({placeholders})", ids)
        await db.commit()
        return cur.rowcount


async def get_storage_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COALESCE(SUM(size),0), COUNT(*) FROM files") as cur:
            row = await cur.fetchone()
            return {"used_bytes": row[0], "file_count": row[1]}
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_database.py -v
```

Expected: 9 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/test_database.py
git commit -m "feat: SQLite database layer with full CRUD"
```

---

## Task 4: Telegram Client

**Files:**
- Create: `backend/telegram.py`
- Create: `tests/test_telegram.py`

- [ ] **Step 1: Write failing tests in `tests/test_telegram.py`**

```python
import pytest
from pytest_httpx import HTTPXMock
from backend.telegram import TelegramClient


@pytest.fixture
def tg():
    return TelegramClient("TESTTOKEN", "-100CHAT")


async def test_send_document(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/sendDocument",
        json={
            "ok": True,
            "result": {
                "message_id": 42,
                "document": {"file_id": "FILE_XYZ", "file_size": 1024},
            },
        },
    )
    result = await tg.send_document("photo.jpg", b"data", "image/jpeg")
    assert result["message_id"] == 42
    assert result["file_id"] == "FILE_XYZ"


async def test_send_document_api_error(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/sendDocument",
        json={"ok": False, "description": "Bad Request: file too large"},
    )
    with pytest.raises(ValueError, match="file too large"):
        await tg.send_document("big.zip", b"data", "application/zip")


async def test_get_file_url(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/getFile",
        json={"ok": True, "result": {"file_path": "documents/file_42.jpg"}},
    )
    url = await tg.get_file_url("FILE_XYZ")
    assert url == "https://api.telegram.org/file/botTESTTOKEN/documents/file_42.jpg"


async def test_download_file(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/getFile",
        json={"ok": True, "result": {"file_path": "documents/file_42.jpg"}},
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/file/botTESTTOKEN/documents/file_42.jpg",
        content=b"image bytes",
    )
    content = await tg.download_file("FILE_XYZ")
    assert content == b"image bytes"


async def test_delete_message(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/deleteMessage",
        json={"ok": True, "result": True},
    )
    ok = await tg.delete_message(42)
    assert ok is True
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_telegram.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.telegram'`

- [ ] **Step 3: Write `backend/telegram.py`**

```python
import httpx
from typing import Optional


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._api = f"https://api.telegram.org/bot{bot_token}"
        self._cdn = f"https://api.telegram.org/file/bot{bot_token}"
        self._chat_id = chat_id

    async def send_document(self, filename: str, content: bytes, mime_type: str) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._api}/sendDocument",
                data={"chat_id": self._chat_id},
                files={"document": (filename, content, mime_type)},
                timeout=60.0,
            )
            r.raise_for_status()
            data = r.json()
        if not data["ok"]:
            raise ValueError(data["description"])
        msg = data["result"]
        doc = (
            msg.get("document")
            or msg.get("video")
            or msg.get("audio")
            or (msg.get("photo") or [{}])[-1]
        )
        return {"message_id": msg["message_id"], "file_id": doc["file_id"]}

    async def get_file_url(self, file_id: str) -> str:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self._api}/getFile", params={"file_id": file_id}, timeout=10.0)
            r.raise_for_status()
            data = r.json()
        if not data["ok"]:
            raise ValueError(data["description"])
        return f"{self._cdn}/{data['result']['file_path']}"

    async def download_file(self, file_id: str) -> bytes:
        url = await self.get_file_url(file_id)
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=60.0)
            r.raise_for_status()
            return r.content

    async def delete_message(self, message_id: int) -> bool:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._api}/deleteMessage",
                json={"chat_id": self._chat_id, "message_id": message_id},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json().get("ok", False)
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_telegram.py -v
```

Expected: 5 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/telegram.py tests/test_telegram.py
git commit -m "feat: Telegram Bot API client"
```

---

## Task 5: FastAPI Application

**Files:**
- Create: `backend/main.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write failing tests in `tests/test_api.py`**

```python
import pytest
import backend.database as db_module
from pathlib import Path
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

db_module.DB_PATH = Path("/tmp/test_vault_api.db")

from backend.main import app
from backend.database import init_db


@pytest.fixture(autouse=True)
async def setup_db():
    db_module.DB_PATH.unlink(missing_ok=True)
    await init_db()
    yield
    db_module.DB_PATH.unlink(missing_ok=True)


@pytest.fixture
def mock_tg():
    tg = AsyncMock()
    tg.send_document.return_value = {"file_id": "TG_FILE_1", "message_id": 100}
    tg.download_file.return_value = b"fake file content"
    tg.delete_message.return_value = True
    with patch("backend.main.get_tg_client", return_value=tg):
        yield tg


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_upload_file(mock_tg, client):
    async with client as c:
        r = await c.post(
            "/api/upload",
            files={"file": ("test.jpg", b"image data", "image/jpeg")},
        )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test.jpg"
    assert data["size"] == len(b"image data")
    assert "id" in data


async def test_upload_file_too_large(mock_tg, client):
    big = b"x" * (21 * 1024 * 1024)
    async with client as c:
        r = await c.post(
            "/api/upload",
            files={"file": ("big.zip", big, "application/zip")},
        )
    assert r.status_code == 413


async def test_list_files_empty(client):
    async with client as c:
        r = await c.get("/api/files")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_files_after_upload(mock_tg, client):
    async with client as c:
        await c.post("/api/upload", files={"file": ("a.jpg", b"data", "image/jpeg")})
        r = await c.get("/api/files")
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "a.jpg"


async def test_list_files_search(mock_tg, client):
    async with client as c:
        await c.post("/api/upload", files={"file": ("vacation.jpg", b"d", "image/jpeg")})
        await c.post("/api/upload", files={"file": ("report.pdf", b"d", "application/pdf")})
        r = await c.get("/api/files?q=vacation")
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "vacation.jpg"


async def test_download_file(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("img.jpg", b"img", "image/jpeg")})
        fid = up.json()["id"]
        r = await c.get(f"/api/files/{fid}/download")
    assert r.status_code == 200
    assert r.content == b"fake file content"
    assert "attachment" in r.headers["content-disposition"]


async def test_download_not_found(client):
    async with client as c:
        r = await c.get("/api/files/9999/download")
    assert r.status_code == 404


async def test_preview_image(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("img.jpg", b"img", "image/jpeg")})
        fid = up.json()["id"]
        r = await c.get(f"/api/files/{fid}/preview")
    assert r.status_code == 200
    assert "inline" in r.headers["content-disposition"]


async def test_preview_unsupported(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("data.zip", b"z", "application/zip")})
        fid = up.json()["id"]
        r = await c.get(f"/api/files/{fid}/preview")
    assert r.status_code == 415


async def test_delete_file(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("del.jpg", b"d", "image/jpeg")})
        fid = up.json()["id"]
        r = await c.delete(f"/api/files/{fid}")
        assert r.status_code == 200
        r2 = await c.get(f"/api/files/{fid}/download")
    assert r2.status_code == 404


async def test_delete_not_found(client):
    async with client as c:
        r = await c.delete("/api/files/9999")
    assert r.status_code == 404


async def test_bulk_delete(mock_tg, client):
    async with client as c:
        up1 = await c.post("/api/upload", files={"file": ("a.jpg", b"d", "image/jpeg")})
        up2 = await c.post("/api/upload", files={"file": ("b.jpg", b"d", "image/jpeg")})
        ids = [up1.json()["id"], up2.json()["id"]]
        r = await c.post("/api/files/bulk-delete", json={"ids": ids})
        assert r.status_code == 200
        assert r.json()["deleted"] == 2
        remaining = await c.get("/api/files")
    assert remaining.json() == []


async def test_storage_stats(mock_tg, client):
    async with client as c:
        await c.post("/api/upload", files={"file": ("a.jpg", b"hello", "image/jpeg")})
        r = await c.get("/api/storage")
    assert r.status_code == 200
    data = r.json()
    assert data["file_count"] == 1
    assert data["used_bytes"] == len(b"hello")
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_api.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.main'`

- [ ] **Step 3: Write `backend/main.py`**

```python
import os
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
        headers={"Content-Disposition": f'attachment; filename="{record.name}"'},
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
        headers={"Content-Disposition": f'inline; filename="{record.name}"'},
    )


@app.delete("/api/files/{file_id}")
async def api_delete(file_id: int):
    record = await get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    tg = get_tg_client()
    await tg.delete_message(record.tg_message_id)
    await delete_file(file_id)
    return {"ok": True}


@app.post("/api/files/bulk-delete")
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
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_api.py -v
```

Expected: 14 tests PASSED.

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: all 28 tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py tests/test_api.py
git commit -m "feat: FastAPI routes — upload, download, delete, preview, search, bulk"
```

---

## Task 6: Frontend — Vault Single-Page App

**Files:**
- Create: `frontend/index.html`

- [ ] **Step 1: Write `frontend/index.html`**

```html
<!doctype html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<title>Vault — Files</title>
<meta name="viewport" content="width=1440">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0a0a0a;--bg-1:#0f0f0f;--bg-2:#141414;
    --line:rgba(255,255,255,0.07);--line-2:rgba(255,255,255,0.12);
    --ink:#f2f2f0;--ink-2:#a8a8a3;--ink-3:#6a6a64;--ink-4:#3d3d39;
    --green:#00ff66;--green-glow:#7dffae;
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--bg);color:var(--ink);font-family:'Inter',sans-serif;-webkit-font-smoothing:antialiased}
  .mono{font-family:'JetBrains Mono',monospace}
  button{font-family:inherit;color:inherit;background:none;border:none;cursor:pointer;padding:0}
  ::selection{background:var(--green);color:#000}

  .wrap{max-width:980px;margin:0 auto;padding:56px 32px 100px;min-height:100vh;display:flex;flex-direction:column;gap:36px}

  /* Header */
  .head{display:flex;align-items:center;gap:16px}
  .mark{width:24px;height:24px;border-radius:6px;background:var(--green);display:grid;place-items:center;color:#001b0b;font-weight:600;font-size:12px;box-shadow:0 0 0 1px rgba(0,255,102,0.3),0 8px 22px -8px rgba(0,255,102,0.5);flex-shrink:0}
  .brand{font-weight:500;letter-spacing:0.04em;font-size:14px}
  .brand small{color:var(--ink-3);font-weight:400;letter-spacing:0;margin-left:8px;font-size:12px}
  .head .grow{flex:1}
  .upload-btn{display:flex;align-items:center;gap:8px;height:32px;padding:0 14px;border-radius:7px;background:var(--green);color:#001b0b;font-weight:500;font-size:13px;transition:background .12s}
  .upload-btn:hover{background:var(--green-glow)}
  #file-input{display:none}

  /* Storage */
  .storage .label{font-size:12px;letter-spacing:0.18em;text-transform:uppercase;color:var(--ink-3)}
  .storage .num{font-family:'JetBrains Mono',monospace;font-size:44px;letter-spacing:-0.025em;line-height:1}
  .storage .num small{color:var(--ink-3);font-size:18px;margin-left:4px}
  .bar{margin-top:14px;height:3px;background:#1a1a1a;border-radius:99px;position:relative;overflow:hidden}
  .bar i{position:absolute;left:0;top:0;height:100%;background:var(--green);transition:width .4s}

  /* Toolbar */
  .toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .search{display:flex;align-items:center;gap:10px;border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:var(--bg-1);font-size:13px;flex:1;max-width:340px}
  .search input{background:none;border:0;outline:none;color:var(--ink);flex:1;font:inherit}
  .search input::placeholder{color:var(--ink-3)}
  .toolbar .grow{flex:1}
  .tabs{display:flex;gap:4px}
  .tab{padding:6px 12px;font-size:12.5px;color:var(--ink-3);border-radius:6px;cursor:pointer;transition:color .1s,background .1s}
  .tab:hover{color:var(--ink-2)}
  .tab.on{color:var(--ink);background:rgba(255,255,255,0.04)}
  .tab .c{font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--ink-4);margin-left:6px}
  .tab.on .c{color:var(--ink-3)}
  .ico-btn{width:32px;height:32px;border-radius:7px;border:1px solid var(--line);display:grid;place-items:center;color:var(--ink-3);background:transparent;transition:color .1s,border-color .1s}
  .ico-btn:hover{color:var(--ink);border-color:var(--line-2)}

  /* File list */
  .list{border-top:1px solid var(--line)}
  .list-header{display:grid;grid-template-columns:20px 1fr 110px 130px 90px 32px;gap:16px;padding:10px 4px;border-bottom:1px solid var(--line);font-size:10.5px;letter-spacing:0.18em;text-transform:uppercase;color:var(--ink-3);font-weight:500}
  .list-header span{display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
  .list-header span:hover{color:var(--ink-2)}
  .list-header span.sort-active{color:var(--ink-2)}

  .row{display:grid;grid-template-columns:20px 1fr 110px 130px 90px 32px;gap:16px;align-items:center;padding:14px 4px;border-bottom:1px solid var(--line);font-size:13.5px;cursor:pointer;transition:background .12s;position:relative}
  .row:hover{background:rgba(255,255,255,0.02)}
  .row:last-child{border-bottom:0}
  .row.selected{background:rgba(0,255,102,0.04)}

  .cb{width:14px;height:14px;border-radius:3px;border:1px solid var(--line-2);background:transparent;cursor:pointer;accent-color:var(--green);flex-shrink:0}

  .name-cell{display:flex;align-items:center;gap:14px;min-width:0}
  .ic{width:30px;height:30px;border-radius:7px;display:grid;place-items:center;flex-shrink:0;background:var(--bg-2);border:1px solid var(--line);color:var(--ink-2)}
  .ic.img{color:#d4b78a}.ic.vid{color:#a8c8ff}.ic.doc{color:var(--ink-2)}.ic.zip{color:#c8a8ff}.ic.folder{color:var(--green);background:rgba(0,255,102,0.06);border-color:rgba(0,255,102,0.25)}
  .fname{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .fmeta{display:block;color:var(--ink-3);font-family:'JetBrains Mono',monospace;font-size:10.5px;margin-top:2px}

  .col{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--ink-2)}
  .col.dim{color:var(--ink-3)}
  .col.size{text-align:right}
  .col.size .unit{color:var(--ink-3);margin-left:2px}

  .more-btn{width:24px;height:24px;border-radius:5px;display:grid;place-items:center;color:var(--ink-3);opacity:0;transition:opacity .1s}
  .row:hover .more-btn{opacity:1}
  .more-btn:hover{background:rgba(255,255,255,0.05);color:var(--ink)}

  /* Context menu */
  .ctx-menu{position:fixed;background:var(--bg-1);border:1px solid var(--line-2);border-radius:9px;padding:4px;min-width:160px;z-index:100;box-shadow:0 8px 32px rgba(0,0,0,0.6)}
  .ctx-menu button{display:flex;align-items:center;gap:10px;width:100%;padding:8px 12px;border-radius:6px;font-size:13px;color:var(--ink-2);text-align:left}
  .ctx-menu button:hover{background:rgba(255,255,255,0.05);color:var(--ink)}
  .ctx-menu button.danger:hover{color:#ff6b6b;background:rgba(255,107,107,0.08)}
  .ctx-menu .sep{height:1px;background:var(--line);margin:4px 0}

  /* Bulk bar */
  .bulk-bar{position:fixed;bottom:32px;left:50%;transform:translateX(-50%);background:var(--bg-1);border:1px solid var(--line-2);border-radius:12px;padding:12px 20px;display:flex;align-items:center;gap:16px;font-size:13px;z-index:50;box-shadow:0 8px 32px rgba(0,0,0,0.7);opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;transform:translateX(-50%) translateY(10px)}
  .bulk-bar.show{opacity:1;pointer-events:auto;transform:translateX(-50%) translateY(0)}
  .bulk-bar .count{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--ink-3)}
  .bulk-bar .gap{flex:1}
  .bulk-btn{padding:6px 14px;border-radius:7px;font-size:12.5px;font-weight:500}
  .bulk-btn.del{background:rgba(255,107,107,0.12);color:#ff6b6b}
  .bulk-btn.del:hover{background:rgba(255,107,107,0.2)}
  .bulk-btn.cancel{color:var(--ink-3)}
  .bulk-btn.cancel:hover{color:var(--ink)}

  /* Upload overlay */
  .drop-overlay{position:fixed;inset:0;background:rgba(0,255,102,0.05);border:2px dashed rgba(0,255,102,0.4);z-index:200;display:none;place-items:center;flex-direction:column;gap:12px;font-size:16px;color:var(--green)}
  .drop-overlay.show{display:grid}

  /* Upload progress */
  .upload-toast{position:fixed;bottom:24px;right:24px;background:var(--bg-1);border:1px solid var(--line-2);border-radius:10px;padding:14px 18px;min-width:240px;z-index:300;box-shadow:0 8px 24px rgba(0,0,0,0.6);display:none}
  .upload-toast.show{display:block}
  .upload-toast .ut-name{font-size:12px;color:var(--ink-2);margin-bottom:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ut-bar{height:2px;background:#1a1a1a;border-radius:99px;overflow:hidden}
  .ut-bar i{height:100%;background:var(--green);transition:width .15s;display:block}

  /* Toast */
  .toast-area{position:fixed;top:24px;right:24px;display:flex;flex-direction:column;gap:8px;z-index:400}
  .toast{background:var(--bg-1);border:1px solid var(--line-2);border-radius:8px;padding:10px 16px;font-size:13px;color:var(--ink);box-shadow:0 4px 16px rgba(0,0,0,0.5);animation:slide-in .2s ease}
  .toast.err{border-color:rgba(255,107,107,0.4);color:#ff9f9f}
  @keyframes slide-in{from{opacity:0;transform:translateX(10px)}to{opacity:1;transform:none}}

  /* Preview modal */
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:500;display:none;place-items:center}
  .modal-bg.show{display:grid}
  .modal{background:var(--bg-1);border:1px solid var(--line-2);border-radius:12px;max-width:90vw;max-height:90vh;overflow:auto;position:relative}
  .modal img{max-width:85vw;max-height:85vh;display:block;border-radius:8px}
  .modal iframe{width:80vw;height:80vh;border:none;border-radius:8px}
  .modal-close{position:absolute;top:12px;right:12px;width:28px;height:28px;border-radius:99px;background:rgba(255,255,255,0.08);display:grid;place-items:center;font-size:16px;color:var(--ink-2);z-index:1}
  .modal-close:hover{background:rgba(255,255,255,0.14);color:var(--ink)}

  /* Empty state */
  .empty{padding:80px 0;text-align:center;color:var(--ink-3);font-size:14px}

  /* Footer */
  .foot{margin-top:auto;padding-top:24px;display:flex;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--ink-4)}
  .foot .live{color:var(--green);display:flex;align-items:center;gap:8px}
  .foot .live::before{content:"";width:5px;height:5px;border-radius:99px;background:var(--green);box-shadow:0 0 10px var(--green);animation:pulse 1.6s infinite}
  @keyframes pulse{50%{opacity:.4}}
</style>
</head>
<body>

<!-- SVG Icons -->
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <symbol id="i-search" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></symbol>
  <symbol id="i-up" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 19V5M5 12l7-7 7 7"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></symbol>
  <symbol id="i-img" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m4 18 5-5 4 4 3-3 4 4"/></symbol>
  <symbol id="i-vid" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3z"/></symbol>
  <symbol id="i-doc" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></symbol>
  <symbol id="i-zip" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M12 3v4M12 9v2M12 13v2M12 17v2"/></symbol>
  <symbol id="i-sort" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M7 5v14M3 9l4-4 4 4M17 19V5M13 15l4 4 4-4"/></symbol>
  <symbol id="i-down" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14M5 12l7 7 7-7"/></symbol>
  <symbol id="i-trash" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></symbol>
  <symbol id="i-eye" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></symbol>
  <symbol id="i-dots" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></symbol>
  <symbol id="i-arrow-d" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></symbol>
  <symbol id="i-arrow-u" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="m6 15 6-6 6 6"/></symbol>
  <symbol id="i-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></symbol>
</defs></svg>

<!-- Drop Overlay -->
<div class="drop-overlay" id="drop-overlay">
  <svg width="32" height="32"><use href="#i-up"/></svg>
  放開以上傳
</div>

<!-- Upload Progress Toast -->
<div class="upload-toast" id="upload-toast">
  <div class="ut-name" id="ut-name">uploading...</div>
  <div class="ut-bar"><i id="ut-prog" style="width:0%"></i></div>
</div>

<!-- Toast Area -->
<div class="toast-area" id="toast-area"></div>

<!-- Context Menu -->
<div class="ctx-menu" id="ctx-menu" style="display:none"></div>

<!-- Bulk Bar -->
<div class="bulk-bar" id="bulk-bar">
  <span class="count" id="bulk-count">0 selected</span>
  <span class="gap"></span>
  <button class="bulk-btn cancel" id="bulk-cancel">取消</button>
  <button class="bulk-btn del" id="bulk-del">
    <svg width="12" height="12"><use href="#i-trash"/></svg> 刪除全部
  </button>
</div>

<!-- Preview Modal -->
<div class="modal-bg" id="modal-bg">
  <div class="modal" id="modal-content">
    <button class="modal-close" id="modal-close"><svg width="12" height="12"><use href="#i-close"/></svg></button>
  </div>
</div>

<!-- Main -->
<div class="wrap">
  <header class="head">
    <div class="mark">V</div>
    <div class="brand">Vault<small>Personal</small></div>
    <div class="grow"></div>
    <button class="upload-btn" id="upload-btn">
      <svg width="13" height="13"><use href="#i-up"/></svg>
      上傳
    </button>
    <input type="file" id="file-input" multiple>
  </header>

  <section class="storage">
    <div class="label">Storage</div>
    <div class="num" id="storage-num">—<small>/ ∞</small></div>
    <div class="bar"><i id="storage-bar" style="width:0%"></i></div>
  </section>

  <section class="toolbar">
    <div class="search">
      <svg width="13" height="13" style="color:var(--ink-3)"><use href="#i-search"/></svg>
      <input id="search-input" placeholder="搜尋檔案…" autocomplete="off">
    </div>
    <div class="grow"></div>
    <div class="tabs" id="tabs">
      <span class="tab on" data-type="">全部<span class="c" id="cnt-all">0</span></span>
      <span class="tab" data-type="image">圖片<span class="c" id="cnt-img">0</span></span>
      <span class="tab" data-type="video">影片<span class="c" id="cnt-vid">0</span></span>
      <span class="tab" data-type="document">文件<span class="c" id="cnt-doc">0</span></span>
    </div>
    <button class="ico-btn" id="sort-btn" title="排序"><svg width="14" height="14"><use href="#i-sort"/></svg></button>
  </section>

  <section class="list" id="file-list-section">
    <div class="list-header">
      <span></span>
      <span data-sort="name" id="h-name">名稱</span>
      <span data-sort="type" id="h-type">類型</span>
      <span data-sort="date" id="h-date" class="sort-active">修改時間 <svg width="8" height="8"><use href="#i-arrow-d"/></svg></span>
      <span data-sort="size" id="h-size" style="justify-content:flex-end">大小</span>
      <span></span>
    </div>
    <div id="file-list"></div>
  </section>

  <footer class="foot">
    <span class="live" id="sync-status">同步中…</span>
    <span>Telegram 儲存</span>
  </footer>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
const state = {
  files: [],
  allFiles: [],
  selected: new Set(),
  sort: 'date',
  order: 'desc',
  type: '',
  q: '',
};

// ── Utilities ──────────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} <span class="unit">B</span>`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} <span class="unit">KB</span>`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(2)} <span class="unit">MB</span>`;
  return `${(bytes / 1024 ** 3).toFixed(2)} <span class="unit">GB</span>`;
}

function fmtDate(iso) {
  const d = new Date(iso), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  if (diff < 172800) return '昨天';
  return d.toLocaleDateString('zh-TW');
}

function mimeIcon(mime) {
  if (!mime) return 'doc';
  if (mime.startsWith('image/')) return 'img';
  if (mime.startsWith('video/')) return 'vid';
  if (mime === 'application/zip' || mime === 'application/x-rar-compressed') return 'zip';
  return 'doc';
}

function mimeLabel(mime) {
  if (!mime) return '檔案';
  const parts = mime.split('/');
  return (parts[1] || parts[0]).toUpperCase().replace('OCTET-STREAM', '檔案');
}

function isPreviewable(mime) {
  return mime && (mime.startsWith('image/') || mime === 'application/pdf');
}

// ── Toast ──────────────────────────────────────────────────────────────────
function toast(msg, isErr = false) {
  const el = document.createElement('div');
  el.className = `toast${isErr ? ' err' : ''}`;
  el.textContent = msg;
  document.getElementById('toast-area').append(el);
  setTimeout(() => el.remove(), 3000);
}

// ── Context Menu ───────────────────────────────────────────────────────────
let ctxOpen = false;
function openCtx(e, file) {
  e.stopPropagation();
  const menu = document.getElementById('ctx-menu');
  const canPreview = isPreviewable(file.mime_type);
  menu.innerHTML = `
    ${canPreview ? `<button data-action="preview"><svg width="12" height="12"><use href="#i-eye"/></svg> 預覽</button>` : ''}
    <button data-action="download"><svg width="12" height="12"><use href="#i-down"/></svg> 下載</button>
    <div class="sep"></div>
    <button data-action="delete" class="danger"><svg width="12" height="12"><use href="#i-trash"/></svg> 刪除</button>
  `;
  menu.style.display = 'block';
  const x = Math.min(e.clientX, window.innerWidth - 180);
  const y = Math.min(e.clientY, window.innerHeight - 140);
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  ctxOpen = true;
  menu.onclick = (ev) => {
    const action = ev.target.closest('[data-action]')?.dataset.action;
    if (action === 'preview') showPreview(file);
    if (action === 'download') doDownload(file);
    if (action === 'delete') doDelete(file.id);
    closeCtx();
  };
}
function closeCtx() {
  document.getElementById('ctx-menu').style.display = 'none';
  ctxOpen = false;
}
document.addEventListener('click', () => { if (ctxOpen) closeCtx(); });

// ── Preview Modal ──────────────────────────────────────────────────────────
function showPreview(file) {
  const bg = document.getElementById('modal-bg');
  const content = document.getElementById('modal-content');
  let inner = '';
  if (file.mime_type && file.mime_type.startsWith('image/')) {
    inner = `<img src="/api/files/${file.id}/preview" alt="${file.name}">`;
  } else if (file.mime_type === 'application/pdf') {
    inner = `<iframe src="/api/files/${file.id}/preview" title="${file.name}"></iframe>`;
  }
  content.innerHTML = `<button class="modal-close" id="modal-close"><svg width="12" height="12"><use href="#i-close"/></svg></button>${inner}`;
  bg.classList.add('show');
  document.getElementById('modal-close').onclick = closeModal;
}
function closeModal() { document.getElementById('modal-bg').classList.remove('show'); }
document.getElementById('modal-bg').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeModal();
});

// ── Download ───────────────────────────────────────────────────────────────
function doDownload(file) {
  const a = document.createElement('a');
  a.href = `/api/files/${file.id}/download`;
  a.download = file.name;
  a.click();
}

// ── Delete ─────────────────────────────────────────────────────────────────
async function doDelete(id) {
  if (!confirm('確定要刪除這個檔案嗎？')) return;
  const r = await fetch(`/api/files/${id}`, { method: 'DELETE' });
  if (r.ok) { toast('已刪除'); loadFiles(); }
  else { toast('刪除失敗', true); }
}

// ── Bulk Select ────────────────────────────────────────────────────────────
function updateBulkBar() {
  const bar = document.getElementById('bulk-bar');
  const n = state.selected.size;
  document.getElementById('bulk-count').textContent = `已選 ${n} 個`;
  bar.classList.toggle('show', n > 0);
}

document.getElementById('bulk-cancel').onclick = () => {
  state.selected.clear();
  renderFiles();
};

document.getElementById('bulk-del').onclick = async () => {
  if (!confirm(`確定要刪除 ${state.selected.size} 個檔案嗎？`)) return;
  const ids = Array.from(state.selected);
  const r = await fetch('/api/files/bulk-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (r.ok) {
    const data = await r.json();
    toast(`已刪除 ${data.deleted} 個檔案`);
    state.selected.clear();
    loadFiles();
  } else {
    toast('批次刪除失敗', true);
  }
};

// ── Upload ─────────────────────────────────────────────────────────────────
async function uploadFile(file) {
  const MAX = 20 * 1024 * 1024;
  if (file.size > MAX) { toast(`${file.name} 超過 20 MB 限制`, true); return; }

  const toast_el = document.getElementById('upload-toast');
  document.getElementById('ut-name').textContent = file.name;
  document.getElementById('ut-prog').style.width = '0%';
  toast_el.classList.add('show');

  const xhr = new XMLHttpRequest();
  const form = new FormData();
  form.append('file', file);

  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) {
      document.getElementById('ut-prog').style.width = `${(e.loaded / e.total * 100).toFixed(0)}%`;
    }
  });

  await new Promise((resolve) => {
    xhr.onload = () => {
      toast_el.classList.remove('show');
      if (xhr.status === 201) { toast(`${file.name} 上傳成功`); loadFiles(); }
      else {
        try { toast(JSON.parse(xhr.responseText).detail || '上傳失敗', true); }
        catch { toast('上傳失敗', true); }
      }
      resolve();
    };
    xhr.onerror = () => { toast_el.classList.remove('show'); toast('網路錯誤', true); resolve(); };
    xhr.open('POST', '/api/upload');
    xhr.send(form);
  });
}

document.getElementById('upload-btn').onclick = () => document.getElementById('file-input').click();
document.getElementById('file-input').onchange = async (e) => {
  for (const f of e.target.files) await uploadFile(f);
  e.target.value = '';
};

// Drag and drop
document.addEventListener('dragover', (e) => { e.preventDefault(); document.getElementById('drop-overlay').classList.add('show'); });
document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) document.getElementById('drop-overlay').classList.remove('show'); });
document.addEventListener('drop', async (e) => {
  e.preventDefault();
  document.getElementById('drop-overlay').classList.remove('show');
  for (const f of e.dataTransfer.files) await uploadFile(f);
});

// ── Sort ───────────────────────────────────────────────────────────────────
function updateSortHeaders() {
  ['name','type','date','size'].forEach(k => {
    const el = document.getElementById(`h-${k}`);
    if (!el) return;
    const active = state.sort === k;
    el.classList.toggle('sort-active', active);
    const icon = el.querySelector('svg use');
    if (active) {
      if (!icon) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.setAttribute('width','8'); svg.setAttribute('height','8');
        const use = document.createElementNS('http://www.w3.org/2000/svg','use');
        use.setAttribute('href', state.order === 'asc' ? '#i-arrow-u' : '#i-arrow-d');
        svg.appendChild(use); el.appendChild(svg);
      } else {
        icon.setAttribute('href', state.order === 'asc' ? '#i-arrow-u' : '#i-arrow-d');
      }
    } else {
      const svg = el.querySelector('svg'); if (svg) svg.remove();
    }
  });
}

document.querySelector('.list-header').addEventListener('click', (e) => {
  const col = e.target.closest('[data-sort]');
  if (!col) return;
  const key = col.dataset.sort;
  if (state.sort === key) state.order = state.order === 'desc' ? 'asc' : 'desc';
  else { state.sort = key; state.order = 'desc'; }
  loadFiles();
});

// ── Tabs ───────────────────────────────────────────────────────────────────
document.getElementById('tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  tab.classList.add('on');
  state.type = tab.dataset.type;
  state.selected.clear();
  loadFiles();
});

// ── Search ─────────────────────────────────────────────────────────────────
let searchTimer;
document.getElementById('search-input').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = e.target.value.trim(); loadFiles(); }, 250);
});

// ── Render ─────────────────────────────────────────────────────────────────
function renderFiles() {
  const list = document.getElementById('file-list');
  if (state.files.length === 0) {
    list.innerHTML = '<div class="empty">沒有檔案</div>';
    return;
  }
  list.innerHTML = state.files.map(f => {
    const icon = mimeIcon(f.mime_type);
    const sel = state.selected.has(f.id);
    return `
    <div class="row${sel ? ' selected' : ''}" data-id="${f.id}">
      <input type="checkbox" class="cb" data-cb="${f.id}" ${sel ? 'checked' : ''}>
      <div class="name-cell">
        <div class="ic ${icon}"><svg width="14" height="14"><use href="#i-${icon}"/></svg></div>
        <div>
          <div class="fname">${f.name}</div>
          <span class="fmeta">${mimeLabel(f.mime_type)}</span>
        </div>
      </div>
      <div class="col dim">${mimeLabel(f.mime_type)}</div>
      <div class="col">${fmtDate(f.uploaded_at)}</div>
      <div class="col size">${fmtSize(f.size)}</div>
      <button class="more-btn" data-more="${f.id}"><svg width="14" height="14"><use href="#i-dots"/></svg></button>
    </div>`;
  }).join('');

  list.querySelectorAll('.row').forEach(row => {
    const id = parseInt(row.dataset.id);
    const file = state.files.find(f => f.id === id);

    row.querySelector('[data-cb]').addEventListener('click', (e) => {
      e.stopPropagation();
      if (state.selected.has(id)) state.selected.delete(id);
      else state.selected.add(id);
      row.classList.toggle('selected', state.selected.has(id));
      e.target.checked = state.selected.has(id);
      updateBulkBar();
    });

    row.querySelector('[data-more]').addEventListener('click', (e) => openCtx(e, file));

    row.addEventListener('click', (e) => {
      if (e.target.closest('[data-cb]') || e.target.closest('[data-more]')) return;
      if (state.selected.size > 0) {
        if (state.selected.has(id)) state.selected.delete(id);
        else state.selected.add(id);
        row.classList.toggle('selected', state.selected.has(id));
        updateBulkBar();
      } else if (isPreviewable(file.mime_type)) {
        showPreview(file);
      } else {
        doDownload(file);
      }
    });
  });

  updateBulkBar();
  updateSortHeaders();
}

function updateCounts(files) {
  document.getElementById('cnt-all').textContent = files.length;
  document.getElementById('cnt-img').textContent = files.filter(f => f.mime_type?.startsWith('image/')).length;
  document.getElementById('cnt-vid').textContent = files.filter(f => f.mime_type?.startsWith('video/')).length;
  document.getElementById('cnt-doc').textContent = files.filter(f => f.mime_type?.startsWith('application/')).length;
}

// ── Storage Bar ────────────────────────────────────────────────────────────
async function loadStorage() {
  try {
    const r = await fetch('/api/storage');
    const data = await r.json();
    const used = data.used_bytes;
    let label;
    if (used < 1024 ** 2) label = `${(used / 1024).toFixed(1)}<small> KB</small>`;
    else if (used < 1024 ** 3) label = `${(used / 1024 ** 2).toFixed(2)}<small> MB</small>`;
    else label = `${(used / 1024 ** 3).toFixed(2)}<small> GB</small>`;
    document.getElementById('storage-num').innerHTML = label;
    // We don't know total, so show a minimal bar based on file count
    const pct = Math.min(data.file_count * 2, 95);
    document.getElementById('storage-bar').style.width = `${pct}%`;
  } catch {}
}

// ── Load Files ─────────────────────────────────────────────────────────────
async function loadFiles() {
  const params = new URLSearchParams({ sort: state.sort, order: state.order });
  if (state.q) params.set('q', state.q);
  if (state.type) params.set('type', state.type);

  try {
    const r = await fetch(`/api/files?${params}`);
    state.files = await r.json();
    // Fetch all for tab counts (no type filter)
    if (state.type || state.q) {
      const all = await fetch('/api/files');
      state.allFiles = await all.json();
    } else {
      state.allFiles = state.files;
    }
    updateCounts(state.allFiles);
    renderFiles();
    document.getElementById('sync-status').textContent = `已同步 · 剛才`;
  } catch {
    toast('無法連線到伺服器', true);
  }
}

// ── Init ───────────────────────────────────────────────────────────────────
loadFiles();
loadStorage();
</script>
</body>
</html>
```

- [ ] **Step 2: Smoke-test the frontend renders**

Start the server (requires `.env` with valid `BOT_TOKEN` and `CHAT_ID`):
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` in a browser. Verify:
- Dark background with green "V" mark loads
- Storage section visible
- Search bar and type tabs visible
- Empty state shows "沒有檔案"
- No console errors

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: Vault single-page frontend — upload, download, delete, preview, search, sort, multi-select"
```

---

## Task 7: README and Final Wiring

**Files:**
- Create: `README.md`
- Create: `.gitignore`

- [ ] **Step 1: Write `.gitignore`**

```
.env
vault.db
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 2: Write `README.md`**

```markdown
# Telegram Cloud Drive

個人 LAN 雲端硬碟 — 以 Telegram bot 為檔案儲存後端，Vault 深色介面。

## 快速開始

### 1. 前置需求

- Python 3.11+
- Telegram bot token（透過 [@BotFather](https://t.me/botfather) 建立）

### 2. 設定 Telegram 頻道

1. 建立一個**私人頻道**（Private Channel）
2. 將 bot 加入頻道，設為管理員，開啟「發佈訊息」與「刪除訊息」權限
3. 取得頻道的 `chat_id`：
   - 轉發頻道任一訊息給 [@getidsbot](https://t.me/getidsbot)
   - 或呼叫 `https://api.telegram.org/bot<TOKEN>/getUpdates` 取得

### 3. 安裝與啟動

```bash
pip install -r requirements.txt

# 建立 .env
cp .env.example .env
# 填入 BOT_TOKEN 與 CHAT_ID

uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

開啟瀏覽器前往 `http://<LAN-IP>:8000`

### 4. 區域網路存取

在同一網路的裝置可透過主機 IP 存取，例如 `http://192.168.1.100:8000`。

## 功能

| 功能 | 說明 |
|------|------|
| 上傳 | 點擊按鈕或拖放檔案（上限 20 MB） |
| 下載 | 點擊檔案或右鍵選單 |
| 刪除 | 右鍵選單 → 刪除 |
| 預覽 | 圖片與 PDF 可直接在瀏覽器預覽 |
| 搜尋 | 即時搜尋檔名 |
| 排序 | 點擊欄位標題（名稱 / 類型 / 時間 / 大小） |
| 多選 | 勾選 checkbox → 批次刪除 |

## 限制

- 單檔上限 20 MB（Telegram Bot API 標準限制）
- 需大於 20 MB 支援：請自行架設 [Telegram Bot API Server](https://github.com/tdlib/telegram-bot-api)

## 執行測試

```bash
pytest -v
```
```

- [ ] **Step 3: Run full test suite one final time**

```bash
pytest -v
```

Expected output:
```
tests/test_database.py::test_insert_and_get_file PASSED
tests/test_database.py::test_get_file_not_found PASSED
tests/test_database.py::test_list_files_all PASSED
tests/test_database.py::test_list_files_search PASSED
tests/test_database.py::test_list_files_by_type PASSED
tests/test_database.py::test_list_files_sort_by_size PASSED
tests/test_database.py::test_delete_file PASSED
tests/test_database.py::test_bulk_delete PASSED
tests/test_database.py::test_storage_stats PASSED
tests/test_telegram.py::test_send_document PASSED
tests/test_telegram.py::test_send_document_api_error PASSED
tests/test_telegram.py::test_get_file_url PASSED
tests/test_telegram.py::test_download_file PASSED
tests/test_telegram.py::test_delete_message PASSED
tests/test_api.py::test_upload_file PASSED
tests/test_api.py::test_upload_file_too_large PASSED
tests/test_api.py::test_list_files_empty PASSED
tests/test_api.py::test_list_files_after_upload PASSED
tests/test_api.py::test_list_files_search PASSED
tests/test_api.py::test_download_file PASSED
tests/test_api.py::test_download_not_found PASSED
tests/test_api.py::test_preview_image PASSED
tests/test_api.py::test_preview_unsupported PASSED
tests/test_api.py::test_delete_file PASSED
tests/test_api.py::test_delete_not_found PASSED
tests/test_api.py::test_bulk_delete PASSED
tests/test_api.py::test_storage_stats PASSED

27 passed
```

- [ ] **Step 4: Final commit**

```bash
git add .gitignore README.md
git commit -m "docs: README with setup instructions"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Upload (Task 5 + 6)
- ✅ Download (Task 5 + 6)
- ✅ Delete (Task 5 + 6)
- ✅ Preview for images and PDFs (Task 5 + 6)
- ✅ Search (Task 5 + 6)
- ✅ Sort by all columns (Task 5 + 6)
- ✅ Multi-select + bulk delete (Task 6)
- ✅ Telegram Bot API wrapper (Task 4)
- ✅ SQLite metadata store (Task 3)
- ✅ 20 MB limit with error message (Task 5)
- ✅ Storage stats bar (Task 5 + 6)
- ✅ LAN-only, no auth (by design)
- ✅ Dark/green Vault design (Task 6)

**Placeholder scan:** No TBDs, no "fill in later", all code blocks are complete.

**Type consistency:** `FileRecord`, `FileResponse`, `StorageStats`, `BulkDeleteRequest` — all used consistently across tasks 2–5.
