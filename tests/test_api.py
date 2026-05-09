import pytest
import backend.database as db_module
import io
from pathlib import Path
import tempfile
import zipfile
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

db_module.DB_PATH = Path(tempfile.gettempdir()) / "test_vault_api.db"

from backend.main import app
from backend.main import extract_message_file
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


def test_extract_telegram_photo_message():
    info = extract_message_file({
        "message_id": 12,
        "photo": [
            {"file_id": "SMALL", "file_size": 100},
            {"file_id": "MEDIUM", "file_size": 500},
            {"file_id": "LARGE", "file_size": 1000},
        ],
    })
    assert info == {
        "name": "photo_12.jpg",
        "size": 1000,
        "mime_type": "image/jpeg",
        "file_id": "LARGE",
        "thumb_file_id": "MEDIUM",
    }


def test_extract_telegram_video_message():
    info = extract_message_file({
        "message_id": 13,
        "video": {
            "file_id": "VIDEO",
            "file_name": "clip.mov",
            "file_size": 2000,
            "mime_type": "video/quicktime",
            "thumbnail": {"file_id": "THUMB"},
        },
    })
    assert info["name"] == "clip.mov"
    assert info["file_id"] == "VIDEO"
    assert info["thumb_file_id"] == "THUMB"


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


async def test_create_folder(client):
    async with client as c:
        r = await c.post("/api/folders", json={"name": "Photos", "parent_id": None})
        listed = await c.get("/api/folders")
    assert r.status_code == 201
    assert r.json()["name"] == "Photos"
    assert listed.json()[0]["name"] == "Photos"


async def test_delete_folder_recursive(mock_tg, client):
    async with client as c:
        root = await c.post("/api/folders", json={"name": "RootA", "parent_id": None})
        root_id = root.json()["id"]
        child = await c.post("/api/folders", json={"name": "Child", "parent_id": root_id})
        child_id = child.json()["id"]
        up = await c.post(
            "/api/upload",
            data={"folder_id": str(child_id)},
            files={"file": ("inside.txt", b"data", "text/plain")},
        )
        fid = up.json()["id"]
        r = await c.delete(f"/api/folders/{root_id}")
        remaining_files = await c.get("/api/files")
        remaining_root_folders = await c.get("/api/folders")

    assert r.status_code == 200
    assert r.json()["deleted_folders"] == 2
    assert r.json()["deleted_files"] == 1
    assert remaining_files.json() == []
    assert remaining_root_folders.json() == []
    mock_tg.delete_message.assert_awaited_with(100)


async def test_upload_file_to_folder(mock_tg, client):
    async with client as c:
        folder = await c.post("/api/folders", json={"name": "Docs", "parent_id": None})
        folder_id = folder.json()["id"]
        up = await c.post(
            "/api/upload",
            data={"folder_id": str(folder_id)},
            files={"file": ("inside.txt", b"data", "text/plain")},
        )
        root = await c.get("/api/files")
        nested = await c.get(f"/api/files?folder_id={folder_id}")
    assert up.status_code == 201
    assert up.json()["folder_id"] == folder_id
    assert root.json() == []
    assert nested.json()[0]["name"] == "inside.txt"


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


async def test_thumbnail_uses_telegram_thumbnail(mock_tg, client):
    mock_tg.send_document.return_value = {
        "file_id": "TG_FILE_1",
        "thumb_file_id": "TG_THUMB_1",
        "message_id": 100,
    }
    mock_tg.download_file.return_value = b"thumb bytes"
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("clip.mp4", b"vid", "video/mp4")})
        fid = up.json()["id"]
        r = await c.get(f"/api/files/{fid}/thumbnail")
    assert r.status_code == 200
    assert r.content == b"thumb bytes"
    mock_tg.download_file.assert_called_with("TG_THUMB_1")


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


async def test_bulk_download_zip(mock_tg, client):
    mock_tg.download_file.side_effect = [b"file-a", b"file-b"]
    async with client as c:
        up1 = await c.post("/api/upload", files={"file": ("a.txt", b"a", "text/plain")})
        up2 = await c.post("/api/upload", files={"file": ("b.txt", b"b", "text/plain")})
        ids = [up1.json()["id"], up2.json()["id"]]
        r = await c.post("/api/files/bulk-download", json={"ids": ids})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["a.txt", "b.txt"]
        assert zf.read("a.txt") == b"file-a"
        assert zf.read("b.txt") == b"file-b"


async def test_storage_stats(mock_tg, client):
    async with client as c:
        await c.post("/api/upload", files={"file": ("a.jpg", b"hello", "image/jpeg")})
        r = await c.get("/api/storage")
    assert r.status_code == 200
    data = r.json()
    assert data["file_count"] == 1
    assert data["used_bytes"] == len(b"hello")
