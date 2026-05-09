import pytest
from pytest_httpx import HTTPXMock
from backend.telegram import TelegramClient


@pytest.fixture
async def tg():
    client = TelegramClient("TESTTOKEN", "-100CHAT")
    yield client
    await client.aclose()


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
    assert result["thumb_file_id"] is None


async def test_send_document_thumbnail(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/sendDocument",
        json={
            "ok": True,
            "result": {
                "message_id": 42,
                "document": {
                    "file_id": "FILE_XYZ",
                    "thumbnail": {"file_id": "THUMB_XYZ"},
                },
            },
        },
    )
    result = await tg.send_document("photo.jpg", b"data", "image/jpeg")
    assert result["thumb_file_id"] == "THUMB_XYZ"


async def test_send_document_api_error(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/sendDocument",
        json={"ok": False, "description": "Bad Request: file too large"},
    )
    with pytest.raises(ValueError, match="file too large"):
        await tg.send_document("big.zip", b"data", "application/zip")


async def test_send_document_retries_rate_limit(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/sendDocument",
        status_code=429,
        json={
            "ok": False,
            "description": "Too Many Requests",
            "parameters": {"retry_after": 0},
        },
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/sendDocument",
        json={
            "ok": True,
            "result": {
                "message_id": 43,
                "document": {"file_id": "FILE_AFTER_RETRY", "file_size": 1024},
            },
        },
    )
    result = await tg.send_document("photo.jpg", b"data", "image/jpeg")
    assert result["message_id"] == 43
    assert result["file_id"] == "FILE_AFTER_RETRY"


async def test_get_file_url(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/getFile",
        json={"ok": True, "result": {"file_path": "documents/file_42.jpg"}},
    )
    url = await tg.get_file_url("FILE_XYZ")
    assert url == "https://api.telegram.org/file/botTESTTOKEN/documents/file_42.jpg"


async def test_get_updates(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/getUpdates",
        json={"ok": True, "result": [{"update_id": 10, "message": {"message_id": 1}}]},
    )
    updates = await tg.get_updates(offset=9, timeout=0)
    assert updates[0]["update_id"] == 10


async def test_copy_message(httpx_mock: HTTPXMock, tg: TelegramClient):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/copyMessage",
        json={"ok": True, "result": {"message_id": 99}},
    )
    message_id = await tg.copy_message(12345, 7)
    assert message_id == 99


async def test_local_bot_api_base_url(httpx_mock: HTTPXMock):
    tg = TelegramClient("TESTTOKEN", "-100CHAT", api_base_url="http://127.0.0.1:8081/")
    try:
        httpx_mock.add_response(
            url="http://127.0.0.1:8081/botTESTTOKEN/getFile",
            json={"ok": True, "result": {"file_path": "documents/file_42.jpg"}},
        )
        url = await tg.get_file_url("FILE_XYZ")
    finally:
        await tg.aclose()
    assert url == "http://127.0.0.1:8081/file/botTESTTOKEN/documents/file_42.jpg"


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
