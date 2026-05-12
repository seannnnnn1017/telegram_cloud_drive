import pytest
import backend.database as db_module
import asyncio
import io
import os
from pathlib import Path
import tempfile
import zipfile
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

db_module.DB_PATH = Path(tempfile.gettempdir()) / "test_vault_api.db"

from backend.main import app
from backend.main import extract_message_file, ingest_telegram_message
from backend.database import init_db, insert_file, list_deleted_message_ids, list_files
from backend.sync import parse_caption


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
    tg.copy_message.return_value = 101
    tg.send_message.return_value = 102
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


async def test_ingest_telegram_message_writes_identifying_caption_json():
    tg = AsyncMock()
    tg.copy_message.return_value = 200
    message = {
        "message_id": 13,
        "chat": {"id": 12345},
        "video": {
            "file_id": "VIDEO",
            "file_name": "clip.mov",
            "file_size": 2000,
            "mime_type": "video/quicktime",
            "thumbnail": {"file_id": "THUMB"},
        },
    }

    inserted = await ingest_telegram_message(tg, message)

    assert inserted is True
    tg.copy_message.assert_awaited_once()
    copy_kwargs = tg.copy_message.await_args.kwargs
    initial_caption = parse_caption(copy_kwargs["caption"])
    assert initial_caption is not None
    assert initial_caption["name"] == "clip.mov"
    assert initial_caption["tg_file_id"] == "VIDEO"
    assert initial_caption["tg_thumb_file_id"] == "THUMB"
    assert initial_caption["uid"]

    tg.edit_message_caption.assert_awaited_once()
    edit_args = tg.edit_message_caption.await_args.args
    assert edit_args[0] == 200
    final_caption = parse_caption(edit_args[1])
    assert final_caption is not None
    assert final_caption["bot_message_id"] == 200
    assert final_caption["uid"] == initial_caption["uid"]

    files = await list_files()
    assert len(files) == 1
    assert files[0].tg_message_id == 200
    assert files[0].uid == initial_caption["uid"]


async def test_upload_file_splits_large_file(mock_tg, client):
    mock_tg.send_document.side_effect = [
        {"file_id": "TG_MAIN", "message_id": 100},
        {"file_id": "TG_PART_1", "message_id": 101},
    ]

    with patch("backend.main.CHUNK_SIZE", 4):
        async with client as c:
            r = await c.post(
                "/api/upload",
                files={"file": ("big.zip", b"abcdef", "application/zip")},
            )

    assert r.status_code == 201
    assert r.json()["part_count"] == 2
    assert mock_tg.send_document.await_count == 2


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


async def test_ensure_folder_path_creates_nested_folders(client):
    async with client as c:
        r = await c.post("/api/folders/ensure", json={"path": ["Album", "Day 1"], "parent_id": None})
        root = await c.get("/api/folders")
        nested = await c.get(f"/api/folders?parent_id={root.json()[0]['id']}")

    assert r.status_code == 201
    assert r.json()["name"] == "Day 1"
    assert root.json()[0]["name"] == "Album"
    assert nested.json()[0]["name"] == "Day 1"


async def test_ensure_folder_path_reuses_existing_folders(client):
    async with client as c:
        first = await c.post("/api/folders/ensure", json={"path": ["Album", "Day 1"], "parent_id": None})
        second = await c.post("/api/folders/ensure", json={"path": ["Album", "Day 1"], "parent_id": None})
        root = await c.get("/api/folders")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(root.json()) == 1


async def test_ensure_folder_path_handles_parallel_requests(client):
    async with client as c:
        responses = await asyncio.gather(*[
            c.post("/api/folders/ensure", json={"path": ["Album", "Day 1"], "parent_id": None})
            for _ in range(5)
        ])
        root = await c.get("/api/folders")
        root_id = root.json()[0]["id"]
        nested = await c.get(f"/api/folders?parent_id={root_id}")

    ids = {response.json()["id"] for response in responses}
    assert all(response.status_code == 201 for response in responses)
    assert len(ids) == 1
    assert len(root.json()) == 1
    assert len(nested.json()) == 1


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
    mock_tg.delete_message.assert_any_await(100)


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


async def test_download_reassembles_multipart_file(mock_tg, client):
    content = b"abcdefghij"
    mock_tg.send_document.side_effect = [
        {"file_id": "TG_MAIN", "message_id": 100},
        {"file_id": "TG_PART_1", "message_id": 101},
        {"file_id": "TG_PART_2", "message_id": 102},
    ]

    with patch("backend.main.CHUNK_SIZE", 4):
        async with client as c:
            up = await c.post("/api/upload", files={"file": ("large.bin", content, "application/octet-stream")})
            fid = up.json()["id"]
            mock_tg.download_file.side_effect = [b"abcd", b"efgh", b"ij"]
            r = await c.get(f"/api/files/{fid}/download")

    assert up.status_code == 201
    assert up.json()["part_count"] == 3
    assert r.status_code == 200
    assert r.content == content
    assert [call.args[0] for call in mock_tg.download_file.await_args_list] == [
        "TG_MAIN",
        "TG_PART_1",
        "TG_PART_2",
    ]


async def test_download_rejects_incomplete_multipart_file(mock_tg, client):
    fid = await insert_file(
        name="broken.bin",
        size=10,
        mime_type="application/octet-stream",
        tg_file_id="TG_MAIN",
        tg_message_id=100,
        uploaded_at="2026-05-12T00:00:00+00:00",
        part_count=3,
    )

    async with client as c:
        r = await c.get(f"/api/files/{fid}/download")

    assert r.status_code == 409
    assert "incomplete" in r.json()["detail"]
    mock_tg.download_file.assert_not_awaited()


async def test_download_not_found(client):
    async with client as c:
        r = await c.get("/api/files/9999/download")
    assert r.status_code == 404


async def test_rename_file(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("old.txt", b"d", "text/plain")})
        fid = up.json()["id"]
        renamed = await c.patch(f"/api/files/{fid}", json={"name": "new.txt"})
        listed = await c.get("/api/files")

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "new.txt"
    assert listed.json()[0]["name"] == "new.txt"


async def test_rename_file_rejects_slashes(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("old.txt", b"d", "text/plain")})
        fid = up.json()["id"]
        r = await c.patch(f"/api/files/{fid}", json={"name": "bad/name.txt"})

    assert r.status_code == 400


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
    assert 100 in await list_deleted_message_ids()


async def test_delete_file_keeps_local_delete_when_telegram_delete_fails(mock_tg, client):
    mock_tg.delete_message.side_effect = ValueError("Telegram deleteMessage failed: permission denied")
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("del.jpg", b"d", "image/jpeg")})
        fid = up.json()["id"]
        r = await c.delete(f"/api/files/{fid}")
        remaining = await c.get("/api/files")

    assert r.status_code == 200
    assert r.json()["remote_failed"] == 1
    assert remaining.json() == []
    assert 100 in await list_deleted_message_ids()


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


async def test_bulk_delete_continues_after_one_telegram_delete_failure(mock_tg, client):
    async def delete_side_effect(message_id):
        if message_id == 100:
            raise ValueError("Telegram deleteMessage failed: permission denied")
        return True

    mock_tg.send_document.side_effect = [
        {"file_id": "TG_FILE_1", "message_id": 100},
        {"file_id": "TG_FILE_2", "message_id": 101},
    ]
    mock_tg.delete_message.side_effect = delete_side_effect
    async with client as c:
        up1 = await c.post("/api/upload", files={"file": ("a.jpg", b"d", "image/jpeg")})
        up2 = await c.post("/api/upload", files={"file": ("b.jpg", b"d", "image/jpeg")})
        ids = [up1.json()["id"], up2.json()["id"]]
        r = await c.post("/api/files/bulk-delete", json={"ids": ids})
        remaining = await c.get("/api/files")

    assert r.status_code == 200
    assert r.json()["deleted"] == 2
    assert r.json()["remote_failed"] == 1
    assert remaining.json() == []
    assert {100, 101}.issubset(await list_deleted_message_ids())


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


async def test_update_telegram_settings_writes_env(monkeypatch, tmp_path, client):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("TELECLOUD_ENV_FILE", str(env_path))
    monkeypatch.setenv("TELEGRAM_INGEST_UPDATES", "false")

    async def ok_test(self):
        return {"bot": {"username": "vault_bot"}, "chat": {"id": "-1001"}}

    with patch("backend.telegram.TelegramClient.test_connection", ok_test):
        async with client as c:
            r = await c.put(
                "/api/settings/telegram",
                json={
                    "bot_token": "123:ABC",
                    "chat_id": "-1001",
                    "api_base_url": "https://api.telegram.org",
                },
            )

    assert r.status_code == 200
    assert r.json()["bot_token_set"] is True
    assert r.json()["bot_token"] == "123:ABC"
    assert "BOT_TOKEN=123:ABC" in env_path.read_text()
    assert os.environ["CHAT_ID"] == "-1001"


async def test_get_telegram_settings_reads_env(monkeypatch, client):
    monkeypatch.setenv("BOT_TOKEN", "env-token")
    monkeypatch.setenv("CHAT_ID", "-1002")
    monkeypatch.setenv("TELEGRAM_API_BASE_URL", "https://telegram.example")

    async with client as c:
        r = await c.get("/api/settings/telegram")

    assert r.status_code == 200
    assert r.json() == {
        "bot_token_set": True,
        "bot_token": "env-token",
        "chat_id": "-1002",
        "api_base_url": "https://telegram.example",
    }


async def test_create_and_download_share_link(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("shared.txt", b"d", "text/plain")})
        fid = up.json()["id"]
        share = await c.post(f"/api/files/{fid}/share", json={"expires_in_seconds": 60})
        token = share.json()["token"]
        r = await c.get(f"/api/share/{token}")

    assert share.status_code == 200
    assert r.status_code == 200
    assert r.content == b"fake file content"


async def test_share_settings_controls_share_url(monkeypatch, tmp_path, mock_tg, client):
    monkeypatch.setenv("TELECLOUD_ENV_FILE", str(tmp_path / ".env"))

    async with client as c:
        settings = await c.put("/api/settings/share", json={"base_url": "http://localhost:8765"})
        up = await c.post("/api/upload", files={"file": ("shared.txt", b"d", "text/plain")})
        fid = up.json()["id"]
        share = await c.post(f"/api/files/{fid}/share", json={"expires_in_seconds": 60})

    assert settings.status_code == 200
    assert settings.json()["base_url"] == "http://localhost:8765"
    assert share.json()["url"].startswith("http://localhost:8765/api/share/")
    assert "TELECLOUD_SHARE_BASE_URL=http://localhost:8765" in (tmp_path / ".env").read_text()


async def test_server_settings_update_idle_timeout(monkeypatch, tmp_path, client):
    monkeypatch.setenv("TELECLOUD_ENV_FILE", str(tmp_path / ".env"))
    app.state.idle_timeout_seconds = 900

    async with client as c:
        updated = await c.put("/api/settings/server", json={"idle_timeout_seconds": 300})
        current = await c.get("/api/settings/server")

    assert updated.status_code == 200
    assert updated.json()["idle_timeout_seconds"] == 300
    assert current.json()["idle_timeout_seconds"] == 300
    assert app.state.idle_timeout_seconds == 300
    assert "TELECLOUD_IDLE_TIMEOUT=300" in (tmp_path / ".env").read_text()


async def test_shutdown_server_sets_flag(client):
    app.state.shutdown_requested = False

    async with client as c:
        r = await c.post("/api/server/shutdown")

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert app.state.shutdown_requested is True
    app.state.shutdown_requested = False


async def test_shutdown_server_signals_sse_clients(client):
    from backend.main import _sse_clients

    q = asyncio.Queue()
    _sse_clients.append(q)
    app.state.shutdown_requested = False

    try:
        async with client as c:
            r = await c.post("/api/server/shutdown")

        assert r.status_code == 200
        assert await asyncio.wait_for(q.get(), timeout=1) is None
    finally:
        if q in _sse_clients:
            _sse_clients.remove(q)
        app.state.shutdown_requested = False


async def test_move_file_to_folder_updates_location(mock_tg, client):
    async with client as c:
        folder = await c.post("/api/folders", json={"name": "docs", "parent_id": None})
        up = await c.post("/api/upload", files={"file": ("move.txt", b"d", "text/plain")})
        fid = up.json()["id"]

        moved = await c.post(
            "/api/items/relocate",
            json={"file_ids": [fid], "folder_ids": [], "target_folder_id": folder.json()["id"], "operation": "move"},
        )
        root_files = await c.get("/api/files")
        folder_files = await c.get(f"/api/files?folder_id={folder.json()['id']}")

    assert moved.status_code == 200
    assert moved.json()["moved_files"] == 1
    assert root_files.json() == []
    assert folder_files.json()[0]["id"] == fid


async def test_copy_file_to_folder_creates_new_record(mock_tg, client):
    mock_tg.copy_message.return_value = 201
    async with client as c:
        folder = await c.post("/api/folders", json={"name": "copies", "parent_id": None})
        up = await c.post("/api/upload", files={"file": ("copy.txt", b"d", "text/plain")})
        fid = up.json()["id"]

        copied = await c.post(
            "/api/items/relocate",
            json={"file_ids": [fid], "folder_ids": [], "target_folder_id": folder.json()["id"], "operation": "copy"},
        )
        root_files = await c.get("/api/files")
        folder_files = await c.get(f"/api/files?folder_id={folder.json()['id']}")

    assert copied.status_code == 200
    assert copied.json()["copied_files"] == 1
    assert len(root_files.json()) == 1
    assert len(folder_files.json()) == 1
    assert folder_files.json()[0]["name"] == "copy.txt"
    assert folder_files.json()[0]["id"] != fid


async def test_copy_file_keeps_record_when_caption_update_fails(mock_tg, client):
    mock_tg.copy_message.return_value = 201
    async with client as c:
        folder = await c.post("/api/folders", json={"name": "copies", "parent_id": None})
        up = await c.post("/api/upload", files={"file": ("copy.txt", b"d", "text/plain")})
        fid = up.json()["id"]
        mock_tg.edit_message_caption.side_effect = RuntimeError("caption failed")

        copied = await c.post(
            "/api/items/relocate",
            json={"file_ids": [fid], "folder_ids": [], "target_folder_id": folder.json()["id"], "operation": "copy"},
        )
        folder_files = await c.get(f"/api/files?folder_id={folder.json()['id']}")

    assert copied.status_code == 200
    assert copied.json()["copied_files"] == 1
    assert copied.json()["remote_failed"] == 1
    assert len(folder_files.json()) == 1


async def test_share_link_expiry(mock_tg, client):
    async with client as c:
        up = await c.post("/api/upload", files={"file": ("expired.txt", b"d", "text/plain")})
        fid = up.json()["id"]
        share = await c.post(f"/api/files/{fid}/share", json={"expires_in_seconds": 1})
        token = share.json()["token"]
        await asyncio.sleep(1.1)
        r = await c.get(f"/api/share/{token}")

    assert r.status_code == 410


async def test_encrypted_upload_metadata(mock_tg, client):
    async with client as c:
        r = await c.post(
            "/api/upload",
            data={"encrypted": "true", "original_mime_type": "image/png"},
            files={"file": ("secret.png", b"encrypted bytes", "application/octet-stream")},
        )
        fid = r.json()["id"]
        preview = await c.get(f"/api/files/{fid}/preview")
        download = await c.get(f"/api/files/{fid}/download")

    assert r.status_code == 201
    assert r.json()["encrypted"] is True
    assert r.json()["mime_type"] == "image/png"
    assert r.json()["has_thumbnail"] is False
    assert preview.status_code == 415
    assert download.headers["x-vault-encrypted"] == "1"
