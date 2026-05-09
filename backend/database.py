import os
import aiosqlite
from pathlib import Path
from typing import Optional
from .models import FileRecord, FolderRecord

DB_PATH = Path(os.getenv("VAULT_DB_PATH", "vault.db"))

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
    encrypted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_name ON files(name);
CREATE INDEX IF NOT EXISTS idx_date ON files(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_mime ON files(mime_type);
CREATE TABLE IF NOT EXISTS folders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
CREATE TABLE IF NOT EXISTS shares (
    token      TEXT PRIMARY KEY,
    file_id    INTEGER NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shares_file ON shares(file_id);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        async with db.execute("PRAGMA table_info(files)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "tg_thumb_file_id" not in columns:
            await db.execute("ALTER TABLE files ADD COLUMN tg_thumb_file_id TEXT")
        if "folder_id" not in columns:
            await db.execute("ALTER TABLE files ADD COLUMN folder_id INTEGER")
        if "encrypted" not in columns:
            await db.execute("ALTER TABLE files ADD COLUMN encrypted INTEGER NOT NULL DEFAULT 0")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id)")
        await db.execute(
            "CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL, expires_at TEXT)"
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_shares_file ON shares(file_id)")
        await db.commit()


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
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO files (folder_id, name, size, mime_type, tg_file_id, tg_thumb_file_id, tg_message_id, uploaded_at, encrypted)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (folder_id, name, size, mime_type, tg_file_id, tg_thumb_file_id, tg_message_id, uploaded_at, int(encrypted)),
        )
        await db.commit()
        return cur.lastrowid


async def get_file(file_id: int) -> Optional[FileRecord]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as cur:
            row = await cur.fetchone()
            return FileRecord(**dict(row)) if row else None


async def update_file_name(file_id: int, name: str) -> Optional[FileRecord]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("UPDATE files SET name = ? WHERE id = ?", (name, file_id))
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

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [FileRecord(**dict(r)) for r in await cur.fetchall()]


async def create_folder(name: str, parent_id: Optional[int], created_at: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO folders (parent_id, name, created_at) VALUES (?, ?, ?)",
            (parent_id, name, created_at),
        )
        await db.commit()
        return cur.lastrowid


async def get_folder(folder_id: int) -> Optional[FolderRecord]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)) as cur:
            row = await cur.fetchone()
            return FolderRecord(**dict(row)) if row else None


async def get_folder_by_name(name: str, parent_id: Optional[int]) -> Optional[FolderRecord]:
    async with aiosqlite.connect(DB_PATH) as db:
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


async def list_folder_tree_ids(folder_id: int) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()
        return cur.rowcount > 0


async def bulk_delete_files(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"DELETE FROM files WHERE id IN ({placeholders})", ids)
        await db.commit()
        return cur.rowcount


async def delete_folders(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"DELETE FROM folders WHERE id IN ({placeholders})", ids)
        await db.commit()
        return cur.rowcount


async def get_storage_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COALESCE(SUM(size),0), COUNT(*) FROM files") as cur:
            row = await cur.fetchone()
            return {"used_bytes": row[0], "file_count": row[1]}


async def create_share(token: str, file_id: int, expires_at: Optional[str]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO shares (token, file_id, expires_at) VALUES (?, ?, ?)",
            (token, file_id, expires_at),
        )
        await db.commit()


async def get_share(token: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shares WHERE token = ?", (token,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_share(token: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM shares WHERE token = ?", (token,))
        await db.commit()
        return cur.rowcount > 0
