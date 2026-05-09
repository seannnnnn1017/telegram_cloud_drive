import json
import os
from pathlib import Path
from typing import Optional

from .database import insert_file, list_all_message_ids

CAPTION_VERSION = 1


def make_caption(
    name: str,
    size: int,
    mime_type: Optional[str],
    encrypted: bool,
    uploaded_at: str,
    tg_file_id: Optional[str] = None,
    tg_thumb_file_id: Optional[str] = None,
) -> str:
    data: dict = {
        "v": CAPTION_VERSION,
        "n": name,
        "s": size,
        "m": mime_type or "",
        "e": int(encrypted),
        "t": uploaded_at,
    }
    if tg_file_id:
        data["f"] = tg_file_id
    if tg_thumb_file_id:
        data["th"] = tg_thumb_file_id
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def parse_caption(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("v") != CAPTION_VERSION:
        return None
    # "f" (file_id) is optional — synced via pack_bot_file_id if absent
    if not all(k in data for k in ("n", "s", "t")):
        return None
    return {
        "name": data["n"],
        "size": int(data["s"]),
        "mime_type": data.get("m") or None,
        "tg_file_id": data.get("f") or "",
        "tg_thumb_file_id": data.get("th"),
        "encrypted": bool(data.get("e", 0)),
        "uploaded_at": data["t"],
    }


def _env_path() -> Path:
    p = Path(os.getenv("TELECLOUD_ENV_FILE", ".env"))
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def _update_env_file(path: Path, updates: dict) -> None:
    existing = path.read_text().splitlines() if path.exists() else []
    seen: set = set()
    lines: list = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")


async def sync_from_telegram() -> dict:
    try:
        from telethon import TelegramClient as TelethonClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("telethon is not installed. Run: pip install telethon") from exc

    api_id_raw = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()
    chat_id_raw = os.getenv("CHAT_ID", "").strip()
    session_str = os.getenv("TG_SESSION_STRING", "").strip()

    if not api_id_raw or not api_hash:
        raise ValueError("TG_API_ID 和 TG_API_HASH 尚未設定，請先在設定頁面填入。")
    if not chat_id_raw:
        raise ValueError("CHAT_ID 尚未設定。")
    if not session_str:
        raise ValueError(
            "尚未完成使用者帳號認證。請在終端機執行：\n\n  telecloud sync-auth\n\n"
            "完成手機 OTP 驗證後再同步。"
        )

    api_id = int(api_id_raw)
    client = TelethonClient(StringSession(session_str), api_id, api_hash)
    # connect as user (no bot_token) — user accounts can read message history
    await client.connect()
    if not await client.is_user_authorized():
        raise ValueError(
            "Session 已失效，請重新執行 `telecloud sync-auth` 重新認證。"
        )

    try:
        existing_msg_ids = await list_all_message_ids()

        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            chat_id = chat_id_raw

        imported = 0
        skipped_exists = 0
        skipped_no_caption = 0

        from telethon.utils import pack_bot_file_id

        async for message in client.iter_messages(chat_id):
            if message.id in existing_msg_ids:
                skipped_exists += 1
                continue

            caption = message.message or ""
            parsed = parse_caption(caption)

            if parsed is None:
                skipped_no_caption += 1
                continue

            # Derive Bot-API-compatible file_id from the message media if not in caption
            file_id = parsed["tg_file_id"]
            if not file_id and message.media is not None:
                try:
                    file_id = pack_bot_file_id(message.media)
                except Exception:
                    skipped_no_caption += 1
                    continue

            if not file_id:
                skipped_no_caption += 1
                continue

            await insert_file(
                name=parsed["name"],
                size=parsed["size"],
                mime_type=parsed["mime_type"],
                tg_file_id=file_id,
                tg_thumb_file_id=parsed["tg_thumb_file_id"],
                tg_message_id=message.id,
                uploaded_at=parsed["uploaded_at"],
                encrypted=parsed["encrypted"],
            )
            imported += 1

        return {
            "ok": True,
            "imported": imported,
            "skipped_exists": skipped_exists,
            "skipped_no_caption": skipped_no_caption,
        }
    finally:
        await client.disconnect()
