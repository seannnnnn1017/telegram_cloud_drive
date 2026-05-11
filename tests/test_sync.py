"""Tests for sync.py: caption format, dedup logic, folder path handling,
and the two-server upload → sync scenario."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import backend.database as db_module

# Redirect to a fresh temp DB before any import of database internals
db_module.DB_PATH = Path(tempfile.gettempdir()) / "test_vault_sync.db"

from backend.database import (
    add_deleted_message_id,
    create_folder,
    delete_files_by_message_ids,
    get_file,
    get_folder,
    init_db,
    insert_file,
    list_all_message_ids,
    list_all_uids,
    list_deleted_message_ids,
    list_files,
    list_folders,
)
from backend.sync import (
    _import_message,
    _run_sync,
    make_caption,
    make_folder_caption,
    parse_caption,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeMedia:
    """Minimal stand-in for a Telethon media object."""
    pass


class FakeMessage:
    """Minimal stand-in for a Telethon Message."""

    def __init__(self, id: int, caption: str, media=None):
        self.id = id
        self.message = caption
        self.media = media or FakeMedia()


def _make_tg_caption(
    name: str,
    size: int = 1024,
    mime: str = "image/jpeg",
    uid: str = "abc123",
    file_id: str = "TG_FILE_X",
) -> str:
    """Build a caption the way the upload endpoint does."""
    return make_caption(
        name=name,
        size=size,
        mime_type=mime,
        encrypted=False,
        uploaded_at="2026-01-01T00:00:00+00:00",
        uid=uid,
        tg_file_id=file_id,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def clean_db():
    db_module.DB_PATH.unlink(missing_ok=True)
    await init_db()
    yield
    db_module.DB_PATH.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Caption format
# ---------------------------------------------------------------------------

def test_make_caption_includes_uid():
    cap = make_caption("photo.jpg", 2048, "image/jpeg", False, "2026-01-01T00:00:00+00:00", uid="deadbeef")
    parsed = parse_caption(cap)
    assert parsed is not None
    assert parsed["uid"] == "deadbeef"
    assert parsed["name"] == "photo.jpg"


def test_make_caption_without_uid():
    cap = make_caption("photo.jpg", 2048, "image/jpeg", False, "2026-01-01T00:00:00+00:00")
    parsed = parse_caption(cap)
    assert parsed is not None
    assert parsed["uid"] is None


def test_parse_caption_backward_compat_no_uid_field():
    """Old captions without 'id' field must still parse successfully."""
    import json
    old_cap = json.dumps({"v": 1, "n": "old.txt", "s": 100, "m": "text/plain", "e": 0, "t": "2025-01-01T00:00:00+00:00"})
    parsed = parse_caption(old_cap)
    assert parsed is not None
    assert parsed["uid"] is None
    assert parsed["name"] == "old.txt"


def test_parse_caption_invalid_json():
    assert parse_caption("not json") is None


def test_parse_caption_wrong_version():
    import json
    cap = json.dumps({"v": 99, "n": "x", "s": 1, "t": "t"})
    assert parse_caption(cap) is None


def test_parse_caption_missing_required_fields():
    import json
    cap = json.dumps({"v": 1, "n": "x"})  # missing "s" and "t"
    assert parse_caption(cap) is None


# ---------------------------------------------------------------------------
# list_all_uids
# ---------------------------------------------------------------------------

async def test_list_all_uids_empty():
    uids = await list_all_uids()
    assert uids == set()


async def test_list_all_uids_returns_stored_uids():
    await insert_file("a.jpg", 100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00", uid="uid-1")
    await insert_file("b.jpg", 200, "image/jpeg", "f2", 2, "2026-01-01T00:00:00+00:00", uid="uid-2")
    await insert_file("c.jpg", 300, "image/jpeg", "f3", 3, "2026-01-01T00:00:00+00:00")  # no uid
    uids = await list_all_uids()
    assert uids == {"uid-1", "uid-2"}


# ---------------------------------------------------------------------------
# _import_message — dedup
# ---------------------------------------------------------------------------

async def test_import_message_inserts_new_file():
    cap = _make_tg_caption("photo.jpg", uid="uid-new")
    msg = FakeMessage(id=42, caption=cap)
    result = await _import_message(msg)
    assert result is True
    files = await list_files()
    assert len(files) == 1
    assert files[0].name == "photo.jpg"
    assert files[0].uid == "uid-new"


async def test_import_message_dedup_by_uid():
    """Same uid → second import is skipped, even with a different message_id."""
    cap = _make_tg_caption("photo.jpg", uid="uid-dup")
    await _import_message(FakeMessage(id=1, caption=cap))
    result = await _import_message(FakeMessage(id=2, caption=cap))  # different msg id
    assert result is False
    assert len(await list_files()) == 1


async def test_import_message_dedup_by_message_id_old_format():
    """Old captions without uid fall back to message_id dedup."""
    import json
    old_cap = json.dumps({
        "v": 1, "n": "old.jpg", "s": 500, "m": "image/jpeg", "e": 0,
        "t": "2025-01-01T00:00:00+00:00", "f": "TG_OLD",
    })
    msg = FakeMessage(id=99, caption=old_cap)
    await _import_message(msg)
    result = await _import_message(msg)  # same message_id
    assert result is False
    assert len(await list_files()) == 1


async def test_import_message_skips_no_caption():
    msg = FakeMessage(id=1, caption="plain text, not a caption")
    result = await _import_message(msg)
    assert result is False
    assert len(await list_files()) == 0


async def test_import_message_shared_sets_updated_in_place():
    """When sets are passed, they are updated so batch callers don't re-import."""
    cap = _make_tg_caption("img.jpg", uid="uid-shared")
    known_uids: set = set()
    known_msg_ids: set = set()
    msg = FakeMessage(id=7, caption=cap)
    await _import_message(msg, known_uids, known_msg_ids)
    assert "uid-shared" in known_uids
    assert 7 in known_msg_ids
    # Second call with same sets must skip
    result = await _import_message(msg, known_uids, known_msg_ids)
    assert result is False


# ---------------------------------------------------------------------------
# Folder path handling
# ---------------------------------------------------------------------------

async def test_import_message_creates_folder_from_path():
    cap = _make_tg_caption("images/photo.jpg", uid="uid-folder")
    await _import_message(FakeMessage(id=1, caption=cap))
    folders = await list_folders()
    assert len(folders) == 1
    assert folders[0].name == "images"
    files = await list_files(folder_id=folders[0].id)
    assert len(files) == 1
    assert files[0].name == "photo.jpg"
    assert files[0].folder_id == folders[0].id


async def test_import_message_nested_folder_path():
    cap = _make_tg_caption("docs/2026/report.pdf", uid="uid-nested", mime="application/pdf")
    await _import_message(FakeMessage(id=1, caption=cap))
    root_folders = await list_folders()
    assert len(root_folders) == 1
    assert root_folders[0].name == "docs"
    sub_folders = await list_folders(parent_id=root_folders[0].id)
    assert len(sub_folders) == 1
    assert sub_folders[0].name == "2026"
    files = await list_files(folder_id=sub_folders[0].id)
    assert len(files) == 1
    assert files[0].name == "report.pdf"


async def test_import_message_reuses_existing_folder():
    """Syncing two files in the same folder should not create duplicate folders."""
    cap1 = _make_tg_caption("images/a.jpg", uid="uid-a")
    cap2 = _make_tg_caption("images/b.jpg", uid="uid-b", file_id="TG_FILE_2")
    await _import_message(FakeMessage(id=1, caption=cap1))
    await _import_message(FakeMessage(id=2, caption=cap2))
    folders = await list_folders()
    assert len(folders) == 1  # only one "images" folder
    files = await list_files(folder_id=folders[0].id)
    assert len(files) == 2


async def test_sync_folder_caption_merges_existing_path_folder():
    """A folder created from a file path should be enriched, not duplicated,
    when the empty-folder metadata message appears later."""
    file_cap = _make_tg_caption("empty/a.txt", uid="uid-file", file_id="TG_FILE")
    folder_cap = make_folder_caption(
        name="empty",
        path="empty",
        created_at="2026-01-01T00:00:00+00:00",
        uid="uid-folder",
        bot_message_id=8,
    )

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter([
        FakeMessage(id=7, caption=file_cap),
        FakeMessage(id=8, caption=folder_cap, media=None),
    ])

    result = await _run_sync(mock_client, object())

    assert result["imported"] == 1
    folders = await list_folders()
    assert len(folders) == 1
    assert folders[0].name == "empty"
    assert folders[0].uid == "uid-folder"
    assert folders[0].tg_message_id == 8
    files = await list_files(folder_id=folders[0].id)
    assert len(files) == 1


async def test_sync_folder_caption_dedups_by_uid():
    folder_cap = make_folder_caption(
        name="empty",
        path="empty",
        created_at="2026-01-01T00:00:00+00:00",
        uid="uid-folder",
        bot_message_id=8,
    )

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter([
        FakeMessage(id=8, caption=folder_cap, media=None),
        FakeMessage(id=9, caption=folder_cap, media=None),
    ])

    await _run_sync(mock_client, object())
    folders = await list_folders()
    assert len(folders) == 1
    assert folders[0].uid == "uid-folder"


# ---------------------------------------------------------------------------
# Two-server simulation
# ---------------------------------------------------------------------------

async def _fake_iter(messages):
    """Async generator that yields pre-built FakeMessage objects."""
    for m in messages:
        yield m


async def test_two_server_basic_sync():
    """
    Server A uploads a file (inserts to DB, writes caption to Telegram).
    Server B starts with an empty DB and syncs — it should import the file.
    """
    # --- Server A: upload ---
    uid_a = "server-a-uid-001"
    caption = _make_tg_caption("holiday.jpg", uid=uid_a, file_id="TG_HOLIDAY")
    # Server A inserts into its own DB (our test DB acts as Server A here)
    await insert_file("holiday.jpg", 1024, "image/jpeg", "TG_HOLIDAY", 55,
                      "2026-01-01T00:00:00+00:00", uid=uid_a)

    # --- Server B: empty DB ---
    # Wipe DB to simulate a fresh Server B
    db_module.DB_PATH.unlink(missing_ok=True)
    await init_db()

    # Simulate Telegram history: one message with the caption from Server A
    tg_messages = [FakeMessage(id=55, caption=caption)]

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)
    mock_entity = object()

    result = await _run_sync(mock_client, mock_entity)

    assert result["ok"] is True
    assert result["imported"] == 1
    assert result["skipped_exists"] == 0

    files = await list_files()
    assert len(files) == 1
    assert files[0].name == "holiday.jpg"
    assert files[0].uid == uid_a


async def test_two_server_no_duplicates_on_repeated_sync():
    """Running sync twice must not import the same file twice."""
    uid = "uid-once-only"
    caption = _make_tg_caption("doc.pdf", uid=uid, file_id="TG_DOC", mime="application/pdf")
    tg_messages = [FakeMessage(id=10, caption=caption)]

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    await _run_sync(mock_client, object())
    result2 = await _run_sync(mock_client, object())

    assert result2["imported"] == 0
    assert result2["skipped_exists"] == 1
    assert len(await list_files()) == 1


async def test_two_server_sync_with_folder_structure():
    """
    Server A uploads 'photos/trip/beach.jpg'.
    Server B syncs and the file lands in photos/trip/ folder hierarchy.
    """
    uid = "uid-beach"
    caption = _make_tg_caption("photos/trip/beach.jpg", uid=uid, file_id="TG_BEACH")
    tg_messages = [FakeMessage(id=20, caption=caption)]

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    result = await _run_sync(mock_client, object())
    assert result["imported"] == 1

    # Check folder tree
    root = await list_folders()
    assert len(root) == 1 and root[0].name == "photos"
    mid = await list_folders(parent_id=root[0].id)
    assert len(mid) == 1 and mid[0].name == "trip"
    files = await list_files(folder_id=mid[0].id)
    assert len(files) == 1 and files[0].name == "beach.jpg"


async def test_two_server_uid_dedup_across_message_ids():
    """
    If the same file (same uid) appears with two different message_ids
    (e.g. forwarded), only one record should be imported.
    """
    uid = "uid-forwarded"
    caption = _make_tg_caption("img.jpg", uid=uid, file_id="TG_IMG")
    tg_messages = [
        FakeMessage(id=30, caption=caption),
        FakeMessage(id=31, caption=caption),  # same uid, different msg id
    ]

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    result = await _run_sync(mock_client, object())
    assert result["imported"] == 1
    assert result["skipped_exists"] == 1
    assert len(await list_files()) == 1


async def test_two_server_mixed_old_and_new_captions():
    """
    Channel has a mix of old captions (no uid) and new captions (with uid).
    Both should be imported correctly, with old ones deduped by message_id.
    """
    import json

    old_cap = json.dumps({
        "v": 1, "n": "legacy.txt", "s": 50, "m": "text/plain", "e": 0,
        "t": "2025-06-01T00:00:00+00:00", "f": "TG_LEGACY",
    })
    new_cap = _make_tg_caption("modern.jpg", uid="uid-modern", file_id="TG_MODERN")

    tg_messages = [
        FakeMessage(id=1, caption=old_cap),
        FakeMessage(id=2, caption=new_cap),
    ]

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    result = await _run_sync(mock_client, object())
    assert result["imported"] == 2
    files = await list_files()
    names = {f.name for f in files}
    assert names == {"legacy.txt", "modern.jpg"}


# ---------------------------------------------------------------------------
# Deletion sync
# ---------------------------------------------------------------------------

async def test_delete_files_by_message_ids():
    await insert_file("a.jpg", 100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    await insert_file("b.jpg", 100, "image/jpeg", "f2", 2, "2026-01-01T00:00:00+00:00")
    await insert_file("c.jpg", 100, "image/jpeg", "f3", 3, "2026-01-01T00:00:00+00:00")
    deleted = await delete_files_by_message_ids({1, 3})
    assert deleted == 2
    files = await list_files()
    assert len(files) == 1
    assert files[0].name == "b.jpg"


async def test_delete_files_by_message_ids_empty_set():
    await insert_file("a.jpg", 100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00")
    deleted = await delete_files_by_message_ids(set())
    assert deleted == 0
    assert len(await list_files()) == 1


async def test_run_sync_deletes_orphaned_records():
    """Files in the DB whose Telegram messages no longer exist are removed."""
    # Insert 3 files — message IDs 1, 2, 3
    await insert_file("keep.jpg",   100, "image/jpeg", "f1", 1, "2026-01-01T00:00:00+00:00", uid="uid-1")
    await insert_file("keep2.jpg",  100, "image/jpeg", "f2", 2, "2026-01-01T00:00:00+00:00", uid="uid-2")
    await insert_file("delete.jpg", 100, "image/jpeg", "f3", 3, "2026-01-01T00:00:00+00:00", uid="uid-3")

    # Telegram now only has messages 1 and 2 (message 3 was deleted)
    tg_messages = [
        FakeMessage(id=1, caption=_make_tg_caption("keep.jpg",  uid="uid-1", file_id="f1")),
        FakeMessage(id=2, caption=_make_tg_caption("keep2.jpg", uid="uid-2", file_id="f2")),
    ]
    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    result = await _run_sync(mock_client, object())

    assert result["deleted"] == 1
    assert result["imported"] == 0
    files = await list_files()
    assert len(files) == 2
    names = {f.name for f in files}
    assert "delete.jpg" not in names


async def test_run_sync_deletes_orphaned_folder_records():
    await create_folder(
        name="gone",
        parent_id=None,
        created_at="2026-01-01T00:00:00+00:00",
        uid="uid-folder-gone",
        tg_message_id=44,
    )

    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter([])

    result = await _run_sync(mock_client, object())

    assert result["deleted"] == 1
    assert await list_folders() == []


async def test_run_sync_deleted_field_zero_when_nothing_removed():
    cap = _make_tg_caption("photo.jpg", uid="uid-x", file_id="TG_X")
    tg_messages = [FakeMessage(id=5, caption=cap)]
    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    result = await _run_sync(mock_client, object())
    assert result["deleted"] == 0


async def test_two_server_deletion_sync():
    """
    Server A deletes a file (removes the Telegram message).
    Server B syncs — the record that had that message_id should be removed.
    """
    # Server B already has 2 files synced from a previous run
    await insert_file("alive.jpg",   500, "image/jpeg", "fA", 10, "2026-01-01T00:00:00+00:00", uid="uid-alive")
    await insert_file("deleted.jpg", 500, "image/jpeg", "fB", 11, "2026-01-01T00:00:00+00:00", uid="uid-deleted")

    # Server A deleted message 11 — Telegram now only returns message 10
    tg_messages = [
        FakeMessage(id=10, caption=_make_tg_caption("alive.jpg", uid="uid-alive", file_id="fA")),
    ]
    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    result = await _run_sync(mock_client, object())

    assert result["deleted"] == 1
    assert result["imported"] == 0
    files = await list_files()
    assert len(files) == 1
    assert files[0].name == "alive.jpg"


# ---------------------------------------------------------------------------
# Tombstone (deleted_messages) tests
# ---------------------------------------------------------------------------

async def test_tombstone_prevents_reimport_after_delete():
    """After a file is deleted (tombstone written), sync must not re-import it
    even when the Telegram message is still present in the channel."""
    uid = "uid-tombstone"
    cap = _make_tg_caption("ghost.jpg", uid=uid, file_id="TG_GHOST")

    # Simulate: file was uploaded, then deleted locally (tombstone written)
    await add_deleted_message_id(42)

    # Telegram still has the message (deletion failed or slow to propagate)
    tg_messages = [FakeMessage(id=42, caption=cap)]
    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter(tg_messages)

    result = await _run_sync(mock_client, object())

    assert result["imported"] == 0
    assert len(await list_files()) == 0


async def test_tombstone_cleanup_when_telegram_message_gone():
    """When a tombstoned message is confirmed absent from Telegram,
    the tombstone should be cleaned up."""
    await add_deleted_message_id(99)
    assert 99 in await list_deleted_message_ids()

    # Telegram returns no messages (message 99 is truly gone)
    mock_client = AsyncMock()
    mock_client.iter_messages = lambda entity: _fake_iter([])

    await _run_sync(mock_client, object())

    assert 99 not in await list_deleted_message_ids()


async def test_import_message_respects_tombstone():
    """_import_message must refuse to insert a tombstoned message."""
    await add_deleted_message_id(55)
    cap = _make_tg_caption("blocked.jpg", uid="uid-blocked", file_id="TG_B")
    result = await _import_message(FakeMessage(id=55, caption=cap))
    assert result is False
    assert len(await list_files()) == 0
