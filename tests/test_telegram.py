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
