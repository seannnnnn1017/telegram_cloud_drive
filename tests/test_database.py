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
