import pytest
import backend.database as db_module
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

db_module.DB_PATH = Path(tempfile.gettempdir()) / "test_vault_api.db"

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
