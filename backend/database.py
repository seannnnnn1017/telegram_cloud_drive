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
