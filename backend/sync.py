import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .database import (
    create_folder,
    delete_files_by_message_ids,
    get_folder,
    get_folder_by_name,
    insert_file,
    list_all_message_ids,
    list_all_uids,
    list_deleted_message_ids,
    remove_deleted_message_ids,
)

CAPTION_VERSION = 1


def make_caption(
    name: str,
    size: int,
    mime_type: Optional[str],
    encrypted: bool,
    uploaded_at: str,
    uid: Optional[str] = None,
    tg_file_id: Optional[str] = None,
    tg_thumb_file_id: Optional[str] = None,
    bot_message_id: Optional[int] = None,
) -> str:
    data: dict = {
        "v": CAPTION_VERSION,
        "n": name,
        "s": size,
        "m": mime_type or "",
        "e": int(encrypted),
        "t": uploaded_at,
    }
    if uid:
        data["id"] = uid
    if tg_file_id:
        data["f"] = tg_file_id
    if tg_thumb_file_id:
        data["th"] = tg_thumb_file_id
    if bot_message_id is not None:
        data["mid"] = bot_message_id
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
    # "f" (file_id) and "id" (uid) are optional
    if not all(k in data for k in ("n", "s", "t")):
        return None
    return {
        "name": data["n"],
        "size": int(data["s"]),
        "mime_type": data.get("m") or None,
        "uid": data.get("id") or None,
        "tg_file_id": data.get("f") or "",
        "tg_thumb_file_id": data.get("th"),
        "encrypted": bool(data.get("e", 0)),
        "uploaded_at": data["t"],
        "bot_message_id": data.get("mid") or None,
    }


async def _ensure_sync_folder_path(parts: list[str]) -> int:
    """Get or create the nested folder hierarchy described by *parts*.

    Returns the id of the leaf (innermost) folder.
    """
    parent_id: Optional[int] = None
    for part in parts:
        folder = await get_folder_by_name(part, parent_id)
        if folder is None:
            now = datetime.now(timezone.utc).isoformat()
            folder_id = await create_folder(name=part, parent_id=parent_id, created_at=now)
            folder = await get_folder(folder_id)
        parent_id = folder.id
    return parent_id  # type: ignore[return-value]


async def _import_message(
    message,
    known_uids: Optional[set[str]] = None,
    known_msg_ids: Optional[set[int]] = None,
    deleted_msg_ids: Optional[set[int]] = None,
) -> bool:
    """Try to import a single Telethon message into the DB.

    *known_uids* / *known_msg_ids* are used for dedup; if None the DB is
    queried fresh (suitable for the event handler where messages are rare).
    Both sets are updated in-place when a record is inserted so that callers
    iterating a batch don't re-import the same file.

    Returns True if the message was inserted.
    """
    from telethon.utils import pack_bot_file_id

    # Never re-import intentionally deleted messages
    if deleted_msg_ids is None:
        deleted_msg_ids = await list_deleted_message_ids()
    if message.id in deleted_msg_ids:
        return False

    caption = message.message or ""
    parsed = parse_caption(caption)
    if parsed is None:
        return False

    if known_uids is None:
        known_uids = await list_all_uids()
    if known_msg_ids is None:
        known_msg_ids = await list_all_message_ids()

    uid = parsed["uid"]
    # uid-based dedup (preferred); fall back to message_id for old-format captions
    if uid:
        if uid in known_uids:
            return False
    elif message.id in known_msg_ids:
        return False

    file_id = parsed["tg_file_id"]
    if not file_id and message.media is not None:
        try:
            file_id = pack_bot_file_id(message.media)
        except Exception:
            return False
    if not file_id:
        return False

    raw_name = parsed["name"]
    folder_id: Optional[int] = None
    if "/" in raw_name:
        *folder_parts, raw_name = raw_name.split("/")
        folder_id = await _ensure_sync_folder_path(folder_parts)

    # Prefer the Bot API message_id embedded in the caption ("mid") so that
    # deleteMessage works correctly from any device (Bot API and MTProto use
    # different message IDs for the same message in private DM chats).
    tg_msg_id = parsed.get("bot_message_id") or message.id

    await insert_file(
        name=raw_name,
        size=parsed["size"],
        mime_type=parsed["mime_type"],
        tg_file_id=file_id,
        tg_thumb_file_id=parsed["tg_thumb_file_id"],
        tg_message_id=tg_msg_id,
        uploaded_at=parsed["uploaded_at"],
        encrypted=parsed["encrypted"],
        folder_id=folder_id,
        uid=uid,
    )
    if uid:
        known_uids.add(uid)
    known_msg_ids.add(message.id)
    return True


async def _run_sync(client, chat_entity) -> dict:
    """Scan the channel history, import missing files, and remove deleted ones."""
    import logging
    log = logging.getLogger("telecloud.sync")

    # Snapshot of DB state before the scan — used to detect orphaned records
    db_msg_ids = await list_all_message_ids()
    known_uids = await list_all_uids()
    known_msg_ids = set(db_msg_ids)  # mutable copy for import dedup
    deleted_msg_ids = await list_deleted_message_ids()  # tombstones — never re-import

    eid = getattr(chat_entity, "id", "?")
    ename = getattr(chat_entity, "title", None) or getattr(chat_entity, "first_name", "?")
    log.warning("sync: entity id=%s name=%r type=%s", eid, ename, type(chat_entity).__name__)

    imported = 0
    skipped_exists = 0
    skipped_no_caption = 0
    total_seen = 0
    telegram_msg_ids: set[int] = set()

    async for message in client.iter_messages(chat_entity):
        total_seen += 1
        telegram_msg_ids.add(message.id)

        # Never re-import intentionally deleted messages
        if message.id in deleted_msg_ids:
            continue

        # Quick pre-filter by message_id
        if message.id in known_msg_ids:
            skipped_exists += 1
            continue

        caption = message.message or ""
        parsed = parse_caption(caption)
        if parsed is None:
            skipped_no_caption += 1
            continue

        uid = parsed["uid"]
        if uid and uid in known_uids:
            skipped_exists += 1
            continue

        did_import = await _import_message(message, known_uids, known_msg_ids, deleted_msg_ids)
        if did_import:
            imported += 1
        else:
            skipped_no_caption += 1

    # Remove local records whose Telegram messages no longer exist
    # (exclude tombstoned IDs — they're already absent from DB intentionally)
    orphaned = db_msg_ids - telegram_msg_ids - deleted_msg_ids
    deleted = await delete_files_by_message_ids(orphaned)
    if deleted:
        log.warning("sync: removed %s orphaned record(s)", deleted)

    # Clean up tombstones for messages confirmed gone from Telegram
    confirmed_gone = deleted_msg_ids - telegram_msg_ids
    await remove_deleted_message_ids(confirmed_gone)

    log.warning(
        "sync: done — total_seen=%s imported=%s deleted=%s skipped_exists=%s skipped_no_caption=%s",
        total_seen, imported, deleted, skipped_exists, skipped_no_caption,
    )
    return {
        "ok": True,
        "imported": imported,
        "deleted": deleted,
        "skipped_exists": skipped_exists,
        "skipped_no_caption": skipped_no_caption,
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


async def _resolve_chat(client, chat_id_raw: str, bot_token: Optional[str] = None):
    """Resolve CHAT_ID to a Telethon entity, trying multiple formats.

    Handles:
    - Channel ID (positive or negative with -100 prefix)
    - Group/supergroup (negative)
    - DM with bot (CHAT_ID == user's own ID → use bot's user ID from BOT_TOKEN)
    - @username strings
    """
    from telethon.tl.types import PeerChannel, PeerChat

    # String username like @channelname
    if not chat_id_raw.lstrip("-").isdigit():
        return await client.get_entity(chat_id_raw)

    chat_id = int(chat_id_raw)

    if chat_id < 0:
        # Bot API channel format: -100XXXXXXXXXX
        abs_str = str(abs(chat_id))
        if abs_str.startswith("100") and len(abs_str) > 12:
            channel_id = int(abs_str[3:])
            try:
                return await client.get_entity(PeerChannel(channel_id))
            except Exception:
                pass
        # Regular group/supergroup
        try:
            return await client.get_entity(PeerChat(abs(chat_id)))
        except Exception:
            pass
        try:
            return await client.get_entity(chat_id)
        except Exception:
            pass
    else:
        # Positive — first check if this is the user's own Telegram ID.
        # If so, files are stored in the DM between user and bot, so the
        # correct Telethon entity is the BOT (not the user themselves).
        # Use iter_dialogs to find the bot — get_entity() alone fails when
        # the entity cache is cold (e.g. fresh server startup).
        try:
            me = await client.get_me()
            if me is not None and chat_id == me.id and bot_token:
                bot_user_id = int(bot_token.split(":")[0])
                async for dialog in client.iter_dialogs():
                    entity = dialog.entity
                    if getattr(entity, "id", None) == bot_user_id:
                        return entity
        except Exception:
            pass

        # Try as channel (channel ID without -100 prefix)
        try:
            return await client.get_entity(PeerChannel(chat_id))
        except Exception:
            pass

        # Try as direct user / group entity
        try:
            return await client.get_entity(chat_id)
        except Exception:
            pass

        # Generic DM fallback via bot token
        if bot_token:
            try:
                bot_user_id = int(bot_token.split(":")[0])
                return await client.get_entity(bot_user_id)
            except Exception:
                pass

    raise ValueError(
        f"Could not resolve CHAT_ID={chat_id_raw!r}. "
        "If you are using a private channel, make sure the Telegram user account "
        "is a member. If using DM storage, ensure BOT_TOKEN is set in .env."
    )


def _build_telethon_client():
    try:
        from telethon import TelegramClient as TelethonClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("telethon is not installed. Run: pip install telethon") from exc

    api_id_raw = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()
    session_str = os.getenv("TG_SESSION_STRING", "").strip()

    if not api_id_raw or not api_hash:
        raise ValueError("TG_API_ID 和 TG_API_HASH 尚未設定，請先在設定頁面填入。")
    if not session_str:
        raise ValueError(
            "尚未完成使用者帳號認證。請在終端機執行：\n\n  telecloud sync-auth\n\n"
            "完成手機 OTP 驗證後再同步。"
        )
    return TelethonClient(StringSession(session_str), int(api_id_raw), api_hash)


async def sync_from_telegram() -> dict:
    chat_id_raw = os.getenv("CHAT_ID", "").strip()
    if not chat_id_raw:
        raise ValueError("CHAT_ID 尚未設定。")

    client = _build_telethon_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise ValueError("Session 已失效，請重新執行 `telecloud sync-auth` 重新認證。")

    try:
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        chat_entity = await _resolve_chat(client, chat_id_raw, bot_token=bot_token)
        return await _run_sync(client, chat_entity)
    finally:
        await client.disconnect()


async def start_sync_listener(on_change=None) -> None:
    """Connect a persistent Telethon user client.

    On startup runs a full history scan (importing missing files and removing
    orphaned records), then listens for real-time events:
    - NewMessage  → import new file
    - MessageDeleted → remove from local DB

    *on_change* is an optional async callable invoked with a result dict
    ``{"imported": N, "deleted": M}`` whenever the local DB changes.

    Runs until the asyncio task is cancelled (e.g. on server shutdown).
    """
    import logging
    log = logging.getLogger("telecloud.sync")

    chat_id_raw = os.getenv("CHAT_ID", "").strip()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not chat_id_raw:
        log.warning("sync listener: CHAT_ID not set, skipping")
        return

    try:
        from telethon import events
        client = _build_telethon_client()
    except (RuntimeError, ValueError) as exc:
        log.warning("sync listener: cannot start — %s", exc)
        return

    async def _notify(result: dict) -> None:
        if on_change and (result.get("imported", 0) > 0 or result.get("deleted", 0) > 0):
            try:
                await on_change(result)
            except Exception as exc:
                log.warning("sync on_change callback error: %s", exc)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            log.warning("sync listener: session expired, please re-run telecloud sync-auth")
            return

        chat_entity = await _resolve_chat(client, chat_id_raw, bot_token=bot_token)

        # Initial history scan
        result = await _run_sync(client, chat_entity)
        log.warning(
            "sync listener startup: imported=%s deleted=%s",
            result["imported"], result["deleted"],
        )
        await _notify(result)

        # Real-time: new uploads
        @client.on(events.NewMessage(chats=chat_entity))
        async def _on_new_message(event):
            try:
                tombstones = await list_deleted_message_ids()
                did_import = await _import_message(
                    event.message, deleted_msg_ids=tombstones
                )
                if did_import:
                    log.warning("sync event: imported message_id=%s", event.message.id)
                    await _notify({"imported": 1, "deleted": 0})
            except Exception as exc:
                log.warning("sync new-message handler error: %s", exc)

        # Real-time: deletions
        @client.on(events.MessageDeleted)
        async def _on_deleted(event):
            try:
                deleted = await delete_files_by_message_ids(set(event.deleted_ids))
                if deleted:
                    log.warning("sync delete event: removed %s file(s)", deleted)
                    await _notify({"imported": 0, "deleted": deleted})
            except Exception as exc:
                log.warning("sync delete handler error: %s", exc)

        log.warning("sync listener: watching for new messages and deletions in real-time")
        await client.run_until_disconnected()

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("sync listener crashed: %s", exc)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
