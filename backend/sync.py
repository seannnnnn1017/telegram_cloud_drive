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
        try:
            me = await client.get_me()
            if me is not None and chat_id == me.id and bot_token:
                bot_user_id = int(bot_token.split(":")[0])
                return await client.get_entity(bot_user_id)
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
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        chat_entity = await _resolve_chat(client, chat_id_raw, bot_token=bot_token)

        imported = 0
        skipped_exists = 0
        skipped_no_caption = 0

        from telethon.utils import pack_bot_file_id

        async for message in client.iter_messages(chat_entity):
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
