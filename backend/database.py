import os
from contextlib import asynccontextmanager
import aiosqlite
from pathlib import Path
from typing import Optional
from .models import FilePartRecord, FileRecord, FolderRecord

DB_PATH = Path(os.getenv("VAULT_DB_PATH", "vault.db"))

_db: Optional[aiosqlite.Connection] = None


@asynccontextmanager
async def _acquire():
    """Yield the shared connection, or a temporary one if not yet initialised.

    Rolls back any uncommitted transaction on the shared connection if the
    caller raises, preventing stale state from leaking to the next operation.
    """
    if _db is not None:
        try:
            yield _db
        except Exception:
            try:
                await _db.rollback()
            except Exception:
                pass
            raise
    else:
        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id      INTEGER,
    name           TEXT    NOT NULL,
    size           INTEGER NOT NULL,
    mime_type      TEXT,
    tg_file_id     TEXT    NOT NULL,
    tg_thumb_file_id TEXT,
    tg_message_id  INTEGER NOT NULL,
    uploaded_at    TEXT    NOT NULL,
    encrypted      INTEGER NOT NULL DEFAULT 0,
    uid            TEXT,
    favorite       INTEGER NOT NULL DEFAULT 0,
    part_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_name ON files(name);
CREATE INDEX IF NOT EXISTS idx_date ON files(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_mime ON files(mime_type);
CREATE TABLE IF NOT EXISTS file_parts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL,
    part_index     INTEGER NOT NULL,
    tg_file_id     TEXT    NOT NULL,
    tg_message_id  INTEGER NOT NULL,
    size           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_parts_file ON file_parts(file_id);
CREATE TABLE IF NOT EXISTS folders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    uid         TEXT,
    tg_message_id INTEGER,
    favorite    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
CREATE TABLE IF NOT EXISTS shares (
    token      TEXT PRIMARY KEY,
    file_id    INTEGER NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shares_file ON shares(file_id);
CREATE TABLE IF NOT EXISTS deleted_messages (
    tg_message_id INTEGER PRIMARY KEY,
    deleted_at    TEXT NOT NULL
);
"""


async def init_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(_SCHEMA)
    async with _db.execute("PRAGMA table_info(files)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    if "tg_thumb_file_id" not in columns:
        await _db.execute("ALTER TABLE files ADD COLUMN tg_thumb_file_id TEXT")
    if "folder_id" not in columns:
        await _db.execute("ALTER TABLE files ADD COLUMN folder_id INTEGER")
    if "encrypted" not in columns:
        await _db.execute("ALTER TABLE files ADD COLUMN encrypted INTEGER NOT NULL DEFAULT 0")
    if "uid" not in columns:
        await _db.execute("ALTER TABLE files ADD COLUMN uid TEXT")
    if "favorite" not in columns:
        await _db.execute("ALTER TABLE files ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    if "part_count" not in columns:
        await _db.execute("ALTER TABLE files ADD COLUMN part_count INTEGER NOT NULL DEFAULT 0")
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS file_parts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER NOT NULL, part_index INTEGER NOT NULL,"
        "tg_file_id TEXT NOT NULL, tg_message_id INTEGER NOT NULL, size INTEGER NOT NULL)"
    )
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_file_parts_file ON file_parts(file_id)")
    await _db.execute(
        """
        DELETE FROM files
        WHERE uid IS NOT NULL
          AND id NOT IN (
            SELECT MIN(id) FROM files WHERE uid IS NOT NULL GROUP BY uid
          )
        """
    )
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id)")
    await _db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_uid_unique ON files(uid) WHERE uid IS NOT NULL")
    async with _db.execute("PRAGMA table_info(folders)") as cur:
        folder_columns = {row[1] for row in await cur.fetchall()}
    if "uid" not in folder_columns:
        await _db.execute("ALTER TABLE folders ADD COLUMN uid TEXT")
    if "tg_message_id" not in folder_columns:
        await _db.execute("ALTER TABLE folders ADD COLUMN tg_message_id INTEGER")
    if "favorite" not in folder_columns:
        await _db.execute("ALTER TABLE folders ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    await _db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_uid_unique ON folders(uid) WHERE uid IS NOT NULL")
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL, expires_at TEXT)"
    )
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_shares_file ON shares(file_id)")
    await _db.commit()


async def insert_file(
    name: str,
    size: int,
    mime_type: Optional[str],
    tg_file_id: str,
    tg_message_id: int,
    uploaded_at: str,
    tg_thumb_file_id: Optional[str] = None,
    folder_id: Optional[int] = None,
    encrypted: bool = False,
    uid: Optional[str] = None,
    favorite: bool = False,
    part_count: int = 0,
) -> int:
    async with _acquire() as db:
        try:
            cur = await db.execute(
                "INSERT INTO files (folder_id, name, size, mime_type, tg_file_id, tg_thumb_file_id, tg_message_id, uploaded_at, encrypted, uid, favorite, part_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (folder_id, name, size, mime_type, tg_file_id, tg_thumb_file_id, tg_message_id, uploaded_at, int(encrypted), uid, int(favorite), part_count),
            )
            await db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            if not uid:
                raise
            async with db.execute("SELECT id FROM files WHERE uid = ?", (uid,)) as cur:
                row = await cur.fetchone()
                if row is None:
                    raise
                return row[0]


async def get_file(file_id: int) -> Optional[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as cur:
            row = await cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def get_file_by_uid(uid: str) -> Optional[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE uid = ?", (uid,)) as cur:
            row = await cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def get_file_by_message_id(tg_message_id: int) -> Optional[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE tg_message_id = ?", (tg_message_id,)) as cur:
            row = await cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def update_file_name(file_id: int, name: str) -> Optional[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("UPDATE files SET name = ? WHERE id = ?", (name, file_id))
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def update_file_sync_metadata(
    file_id: int,
    *,
    name: str,
    folder_id: Optional[int],
    tg_file_id: str,
    tg_thumb_file_id: Optional[str],
    tg_message_id: int,
    favorite: bool,
) -> Optional[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            UPDATE files
            SET name = ?, folder_id = ?, tg_file_id = ?, tg_thumb_file_id = ?,
                tg_message_id = ?, favorite = ?
            WHERE id = ?
            """,
            (name, folder_id, tg_file_id, tg_thumb_file_id, tg_message_id, int(favorite), file_id),
        )
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def list_files(
    q: Optional[str] = None,
    sort: str = "date",
    order: str = "desc",
    file_type: Optional[str] = None,
    folder_id: Optional[int] = None,
) -> list[FileRecord]:
    col_map = {"name": "name", "date": "uploaded_at", "size": "size", "type": "mime_type"}
    sort_col = col_map.get(sort, "uploaded_at")
    direction = "DESC" if order == "desc" else "ASC"
    type_map = {"image": "image/%", "video": "video/%", "document": "application/%"}

    conds, params = [], []
    if folder_id is None:
        conds.append("folder_id IS NULL")
    else:
        conds.append("folder_id = ?")
        params.append(folder_id)
    if q:
        conds.append("name LIKE ?")
        params.append(f"%{q}%")
    if file_type and file_type in type_map:
        conds.append("mime_type LIKE ?")
        params.append(type_map[file_type])

    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    sql = f"SELECT * FROM files {where} ORDER BY {sort_col} {direction}"

    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [FileRecord(**dict(r)) for r in await cur.fetchall()]


async def update_file_favorite(file_id: int, favorite: bool) -> Optional[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("UPDATE files SET favorite = ? WHERE id = ?", (int(favorite), file_id))
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def create_folder(
    name: str,
    parent_id: Optional[int],
    created_at: str,
    uid: Optional[str] = None,
    tg_message_id: Optional[int] = None,
    favorite: bool = False,
) -> int:
    async with _acquire() as db:
        try:
            cur = await db.execute(
                "INSERT INTO folders (parent_id, name, created_at, uid, tg_message_id, favorite) VALUES (?, ?, ?, ?, ?, ?)",
                (parent_id, name, created_at, uid, tg_message_id, int(favorite)),
            )
            await db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            if not uid:
                raise
            async with db.execute("SELECT id FROM folders WHERE uid = ?", (uid,)) as cur:
                row = await cur.fetchone()
                if row is None:
                    raise
                return row[0]


async def get_folder(folder_id: int) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)) as cur:
            row = await cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def get_folder_by_name(name: str, parent_id: Optional[int]) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        if parent_id is None:
            sql = "SELECT * FROM folders WHERE parent_id IS NULL AND name = ?"
            params = (name,)
        else:
            sql = "SELECT * FROM folders WHERE parent_id = ? AND name = ?"
            params = (parent_id, name)
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def get_folder_by_uid(uid: str) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM folders WHERE uid = ?", (uid,)) as cur:
            row = await cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def get_folder_by_message_id(tg_message_id: int) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM folders WHERE tg_message_id = ?", (tg_message_id,)) as cur:
            row = await cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def get_unique_unidentified_folder_by_name(name: str) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM folders
            WHERE name = ? AND uid IS NULL AND tg_message_id IS NULL
            ORDER BY id ASC
            LIMIT 2
            """,
            (name,),
        ) as cur:
            rows = await cur.fetchall()
            if len(rows) != 1:
                return None
            return FolderRecord(**dict(rows[0]))


async def update_folder_metadata(folder_id: int, uid: Optional[str] = None, tg_message_id: Optional[int] = None) -> Optional[FolderRecord]:
    sets, params = [], []
    if uid is not None:
        sets.append("uid = ?")
        params.append(uid)
    if tg_message_id is not None:
        sets.append("tg_message_id = ?")
        params.append(tg_message_id)
    if not sets:
        return await get_folder(folder_id)
    params.append(folder_id)
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id = ?", params)
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def update_file_folder(file_id: int, folder_id: Optional[int]) -> Optional[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("UPDATE files SET folder_id = ? WHERE id = ?", (folder_id, file_id))
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def update_folder_parent(folder_id: int, parent_id: Optional[int]) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (parent_id, folder_id))
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def update_folder_sync_metadata(
    folder_id: int,
    *,
    name: str,
    parent_id: Optional[int],
    uid: str,
    tg_message_id: int,
    favorite: bool,
) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            UPDATE folders
            SET name = ?, parent_id = ?, uid = ?, tg_message_id = ?, favorite = ?
            WHERE id = ?
            """,
            (name, parent_id, uid, tg_message_id, int(favorite), folder_id),
        )
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def update_folder_favorite(folder_id: int, favorite: bool) -> Optional[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("UPDATE folders SET favorite = ? WHERE id = ?", (int(favorite), folder_id))
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.commit()
        async with db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)) as get_cur:
            row = await get_cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def list_folder_tree_ids(folder_id: int) -> list[int]:
    async with _acquire() as db:
        async with db.execute(
            """
            WITH RECURSIVE tree(id) AS (
                SELECT id FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id FROM folders f JOIN tree t ON f.parent_id = t.id
            )
            SELECT id FROM tree ORDER BY id
            """,
            (folder_id,),
        ) as cur:
            return [row[0] for row in await cur.fetchall()]


async def list_files_in_folder_tree(folder_id: int) -> list[FileRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            WITH RECURSIVE tree(id) AS (
                SELECT id FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id FROM folders f JOIN tree t ON f.parent_id = t.id
            )
            SELECT * FROM files WHERE folder_id IN (SELECT id FROM tree) ORDER BY uploaded_at ASC
            """,
            (folder_id,),
        ) as cur:
            return [FileRecord(**dict(r)) for r in await cur.fetchall()]


async def list_folders(parent_id: Optional[int] = None) -> list[FolderRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        if parent_id is None:
            sql = "SELECT * FROM folders WHERE parent_id IS NULL ORDER BY name COLLATE NOCASE ASC"
            params = ()
        else:
            sql = "SELECT * FROM folders WHERE parent_id = ? ORDER BY name COLLATE NOCASE ASC"
            params = (parent_id,)
        async with db.execute(sql, params) as cur:
            return [FolderRecord(**dict(r)) for r in await cur.fetchall()]


async def delete_file(file_id: int) -> bool:
    async with _acquire() as db:
        cur = await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()
        return cur.rowcount > 0


async def bulk_delete_files(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    async with _acquire() as db:
        cur = await db.execute(f"DELETE FROM files WHERE id IN ({placeholders})", ids)
        await db.commit()
        return cur.rowcount


async def delete_folders(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    async with _acquire() as db:
        cur = await db.execute(f"DELETE FROM folders WHERE id IN ({placeholders})", ids)
        await db.commit()
        return cur.rowcount


async def delete_folders_by_message_ids(msg_ids: set[int]) -> int:
    if not msg_ids:
        return 0
    placeholders = ",".join("?" * len(msg_ids))
    async with _acquire() as db:
        cur = await db.execute(
            f"DELETE FROM folders WHERE tg_message_id IN ({placeholders})",
            list(msg_ids),
        )
        await db.commit()
        return cur.rowcount


async def get_folder_path(folder_id: int) -> list[str]:
    """Return folder names from root to leaf for the given folder_id."""
    async with _acquire() as db:
        path: list[str] = []
        current_id: Optional[int] = folder_id
        while current_id is not None:
            async with db.execute(
                "SELECT id, parent_id, name FROM folders WHERE id = ?", (current_id,)
            ) as cur:
                row = await cur.fetchone()
                if row is None:
                    break
                path.append(row[2])
                current_id = row[1]
        return list(reversed(path))


async def list_all_message_ids() -> set[int]:
    async with _acquire() as db:
        async with db.execute("SELECT tg_message_id FROM files") as cur:
            return {row[0] for row in await cur.fetchall()}


async def list_all_folder_message_ids() -> set[int]:
    async with _acquire() as db:
        async with db.execute("SELECT tg_message_id FROM folders WHERE tg_message_id IS NOT NULL") as cur:
            return {row[0] for row in await cur.fetchall()}


async def delete_files_by_message_ids(msg_ids: set[int]) -> int:
    if not msg_ids:
        return 0
    placeholders = ",".join("?" * len(msg_ids))
    async with _acquire() as db:
        cur = await db.execute(
            f"DELETE FROM files WHERE tg_message_id IN ({placeholders})",
            list(msg_ids),
        )
        await db.commit()
        return cur.rowcount


async def list_all_uids() -> set[str]:
    async with _acquire() as db:
        async with db.execute("SELECT uid FROM files WHERE uid IS NOT NULL") as cur:
            return {row[0] for row in await cur.fetchall()}


async def add_deleted_message_id(tg_message_id: int) -> None:
    """Record that a message has been intentionally deleted (tombstone)."""
    from datetime import datetime, timezone
    async with _acquire() as db:
        await db.execute(
            "INSERT OR IGNORE INTO deleted_messages (tg_message_id, deleted_at) VALUES (?, ?)",
            (tg_message_id, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def list_deleted_message_ids() -> set[int]:
    async with _acquire() as db:
        async with db.execute("SELECT tg_message_id FROM deleted_messages") as cur:
            return {row[0] for row in await cur.fetchall()}


async def remove_deleted_message_ids(msg_ids: set[int]) -> None:
    """Clean up tombstones for messages confirmed gone from Telegram."""
    if not msg_ids:
        return
    placeholders = ",".join("?" * len(msg_ids))
    async with _acquire() as db:
        await db.execute(
            f"DELETE FROM deleted_messages WHERE tg_message_id IN ({placeholders})",
            list(msg_ids),
        )
        await db.commit()


async def get_storage_stats() -> dict:
    async with _acquire() as db:
        async with db.execute("SELECT COALESCE(SUM(size),0), COUNT(*) FROM files") as cur:
            row = await cur.fetchone()
            return {"used_bytes": row[0], "file_count": row[1]}


async def create_share(token: str, file_id: int, expires_at: Optional[str]) -> None:
    async with _acquire() as db:
        await db.execute(
            "INSERT INTO shares (token, file_id, expires_at) VALUES (?, ?, ?)",
            (token, file_id, expires_at),
        )
        await db.commit()


async def get_share(token: str) -> Optional[dict]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shares WHERE token = ?", (token,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_share(token: str) -> bool:
    async with _acquire() as db:
        cur = await db.execute("DELETE FROM shares WHERE token = ?", (token,))
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# file_parts
# ---------------------------------------------------------------------------

async def insert_file_part(
    file_id: int,
    part_index: int,
    tg_file_id: str,
    tg_message_id: int,
    size: int,
) -> int:
    async with _acquire() as db:
        cur = await db.execute(
            "INSERT INTO file_parts (file_id, part_index, tg_file_id, tg_message_id, size) VALUES (?, ?, ?, ?, ?)",
            (file_id, part_index, tg_file_id, tg_message_id, size),
        )
        await db.commit()
        return cur.lastrowid


async def list_file_parts(file_id: int) -> list[FilePartRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM file_parts WHERE file_id = ? ORDER BY part_index ASC", (file_id,)
        ) as cur:
            return [FilePartRecord(**dict(r)) for r in await cur.fetchall()]


async def delete_file_parts_by_file_ids(file_ids: list[int]) -> int:
    if not file_ids:
        return 0
    placeholders = ",".join("?" * len(file_ids))
    async with _acquire() as db:
        cur = await db.execute(
            f"DELETE FROM file_parts WHERE file_id IN ({placeholders})", file_ids
        )
        await db.commit()
        return cur.rowcount


async def list_all_part_message_ids() -> set[int]:
    async with _acquire() as db:
        async with db.execute("SELECT tg_message_id FROM file_parts") as cur:
            return {row[0] for row in await cur.fetchall()}


async def delete_file_parts_by_message_ids(msg_ids: set[int]) -> int:
    if not msg_ids:
        return 0
    placeholders = ",".join("?" * len(msg_ids))
    async with _acquire() as db:
        cur = await db.execute(
            f"DELETE FROM file_parts WHERE tg_message_id IN ({placeholders})",
            list(msg_ids),
        )
        await db.commit()
        return cur.rowcount


async def get_file_part_by_message_id(tg_message_id: int) -> Optional[FilePartRecord]:
    async with _acquire() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM file_parts WHERE tg_message_id = ?", (tg_message_id,)
        ) as cur:
            row = await cur.fetchone()
            return FilePartRecord(**dict(row)) if row else None


async def get_file_by_part_uid(uid: str) -> Optional[FileRecord]:
    """Return the parent FileRecord for the given file uid (used when syncing parts)."""
    return await get_file_by_uid(uid)
