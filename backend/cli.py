import argparse
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import suppress
from pathlib import Path

import httpx
from dotenv import dotenv_values, load_dotenv


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_IDLE_TIMEOUT = 15 * 60
REQUIRED_ENV_KEYS = ("BOT_TOKEN", "CHAT_ID")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telecloud",
        description="Run Telegram Cloud Drive as a lightweight local web command.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the Telegram Cloud Drive env file. Defaults to .env.",
    )
    parser.add_argument(
        "--server",
        action="append",
        default=[],
        metavar="NAME=ENV",
        help="Start an additional server profile. Repeat for multiple profiles.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind. Defaults to 127.0.0.1.")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Bind to 0.0.0.0 and use this machine's LAN IP for browser/share URLs.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Preferred port. Defaults to 8000.")
    parser.add_argument(
        "--share-base-url",
        help="Base URL used when creating share links, e.g. http://localhost:8000 or a LAN/public URL.",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=DEFAULT_IDLE_TIMEOUT,
        help="Seconds before idle auto-shutdown. Use 0 to disable. Defaults to 900.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run Telegram bot setup before starting, even when .env already has required keys.",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the web UI automatically.")
    parser.add_argument("--label", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def split_server_spec(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        path = spec
        return Path(path).stem or "telecloud", path
    name, path = spec.split("=", 1)
    return name.strip() or Path(path).stem or "telecloud", path.strip()


def find_available_port(host: str, preferred_port: int) -> int:
    bind_host = host if host not in {"0.0.0.0", "::"} else ""
    for port in range(preferred_port, preferred_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind_host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free port found from {preferred_port} to {preferred_port + 99}")


def browser_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host


def detect_lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())


def effective_host(args: argparse.Namespace) -> str:
    return "0.0.0.0" if args.lan else args.host


def display_host(bind_host: str, use_lan: bool) -> str:
    if use_lan:
        return detect_lan_ip()
    return browser_host(bind_host)


def resolve_idle_timeout(cli_value: int) -> int:
    raw = os.getenv("TELECLOUD_IDLE_TIMEOUT", "").strip()
    if raw and cli_value == DEFAULT_IDLE_TIMEOUT:
        try:
            return int(raw)
        except ValueError:
            return cli_value
    return cli_value


def env_needs_setup(env_path: Path) -> bool:
    values = dotenv_values(env_path) if env_path.exists() else {}
    return any(not (values.get(key) or os.getenv(key, "")).strip() for key in REQUIRED_ENV_KEYS)


def update_env_file(env_path: Path, updates: dict[str, str]) -> None:
    existing = env_path.read_text().splitlines() if env_path.exists() else []
    seen: set[str] = set()
    lines: list[str] = []
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

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines).rstrip() + "\n")


async def telegram_request(api_base_url: str, bot_token: str, method: str, payload: dict) -> dict:
    base = api_base_url.rstrip("/")
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{base}/bot{bot_token}/{method}", json=payload, timeout=35.0)
    data = response.json()
    if not data.get("ok"):
        description = data.get("description", "unknown Telegram error")
        raise RuntimeError(f"Telegram {method} failed: {description}")
    return data


async def capture_chat_id(bot_token: str, api_base_url: str) -> str:
    initial = await telegram_request(
        api_base_url,
        bot_token,
        "getUpdates",
        {"timeout": 0, "allowed_updates": ["message", "channel_post"]},
    )
    offset = max((update["update_id"] for update in initial["result"]), default=-1) + 1

    print("telecloud setup: send any message to your Telegram bot now.", flush=True)
    print("telecloud setup: waiting for the next message to capture CHAT_ID...", flush=True)
    while True:
        data = await telegram_request(
            api_base_url,
            bot_token,
            "getUpdates",
            {"offset": offset, "timeout": 25, "allowed_updates": ["message", "channel_post"]},
        )
        for update in data["result"]:
            offset = update["update_id"] + 1
            message = update.get("message") or update.get("channel_post")
            chat = message.get("chat") if message else None
            if chat and chat.get("id") is not None:
                title = chat.get("title") or chat.get("username") or chat.get("first_name") or "Telegram chat"
                chat_id = str(chat["id"])
                print(f"telecloud setup: captured CHAT_ID={chat_id} from {title}", flush=True)
                return chat_id


async def ensure_settings(env_path: Path, force_setup: bool = False) -> None:
    values = dotenv_values(env_path) if env_path.exists() else {}
    api_base_url = (
        values.get("TELEGRAM_API_BASE_URL")
        or os.getenv("TELEGRAM_API_BASE_URL")
        or "https://api.telegram.org"
    )
    bot_token = (values.get("BOT_TOKEN") or os.getenv("BOT_TOKEN", "")).strip()
    chat_id = (values.get("CHAT_ID") or os.getenv("CHAT_ID", "")).strip()

    if not force_setup and bot_token and chat_id:
        return

    print(f"telecloud setup: configuring {env_path}", flush=True)
    if force_setup or not bot_token:
        while not bot_token:
            bot_token = input("Paste BOT_TOKEN from @BotFather: ").strip()
    if force_setup or not chat_id:
        chat_id = await capture_chat_id(bot_token, api_base_url)

    updates = {
        "BOT_TOKEN": bot_token,
        "CHAT_ID": chat_id,
        "TELEGRAM_API_BASE_URL": api_base_url,
    }
    update_env_file(env_path, updates)
    os.environ.update(updates)
    print(f"telecloud setup: saved {env_path}", flush=True)


def spawn_servers(args: argparse.Namespace) -> int:
    processes: list[subprocess.Popen] = []
    try:
        for index, spec in enumerate(args.server):
            name, env_file = split_server_spec(spec)
            bind_host = effective_host(args)
            port = find_available_port(bind_host, args.port + index)
            cmd = [
                sys.executable,
                "-m",
                "backend.cli",
                "--env-file",
                env_file,
                "--host",
                bind_host,
                "--port",
                str(port),
                "--idle-timeout",
                str(args.idle_timeout),
                "--label",
                name,
            ]
            if args.lan:
                cmd.append("--lan")
            if args.setup:
                cmd.append("--setup")
            if args.no_browser:
                cmd.append("--no-browser")
            if args.share_base_url:
                cmd.extend(["--share-base-url", args.share_base_url])
            processes.append(subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent.parent))

        while processes:
            for process in list(processes):
                code = process.poll()
                if code is not None:
                    processes.remove(process)
                    if code != 0:
                        return code
            time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        for process in processes:
            process.send_signal(signal.SIGINT)
        for process in processes:
            process.wait()
        return 130


async def idle_watchdog(server, app, timeout_seconds: int) -> None:
    app.state.idle_timeout_seconds = timeout_seconds
    while not server.should_exit:
        if getattr(app.state, "shutdown_requested", False):
            print("telecloud: shutdown requested from web UI", flush=True)
            server.should_exit = True
            return
        current_timeout = int(getattr(app.state, "idle_timeout_seconds", timeout_seconds))
        shutdown_event = getattr(app.state, "shutdown_event", None)
        wait_seconds = 1 if current_timeout <= 0 else min(1, current_timeout)
        if shutdown_event is not None:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(wait_seconds)
        if getattr(app.state, "shutdown_requested", False):
            print("telecloud: shutdown requested from web UI", flush=True)
            server.should_exit = True
            return
        if current_timeout <= 0:
            continue
        last_activity = getattr(app.state, "last_activity", time.monotonic())
        current_timeout = int(getattr(app.state, "idle_timeout_seconds", current_timeout))
        if current_timeout > 0 and time.monotonic() - last_activity >= current_timeout:
            print(f"telecloud: idle for {current_timeout}s, shutting down", flush=True)
            server.should_exit = True
            return


async def run_server(args: argparse.Namespace) -> int:
    import uvicorn

    env_path = Path(args.env_file).expanduser()
    if args.setup or env_needs_setup(env_path):
        await ensure_settings(env_path, force_setup=args.setup)

    if env_path.exists():
        load_dotenv(env_path, override=True)
    else:
        load_dotenv()
    os.environ["TELECLOUD_ENV_FILE"] = str(env_path)

    from backend.main import app

    bind_host = effective_host(args)
    port = find_available_port(bind_host, args.port)
    url = f"http://{display_host(bind_host, args.lan)}:{port}"
    if args.share_base_url:
        os.environ["TELECLOUD_SHARE_BASE_URL"] = args.share_base_url.rstrip("/")
    elif args.lan:
        os.environ["TELECLOUD_SHARE_BASE_URL"] = url
    idle_timeout = resolve_idle_timeout(args.idle_timeout)
    os.environ["TELECLOUD_IDLE_TIMEOUT"] = str(idle_timeout)
    label = f" ({args.label})" if args.label else ""
    print(f"telecloud{label}: {url}", flush=True)

    config = uvicorn.Config(
        app,
        host=bind_host,
        port=port,
        log_level=os.getenv("TELECLOUD_LOG_LEVEL", "info"),
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    watchdog = asyncio.create_task(idle_watchdog(server, app, idle_timeout))
    if not args.no_browser:
        webbrowser.open(url)
    try:
        await server.serve()
    finally:
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog
    return 0


async def run_inspect_messages(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="telecloud inspect-messages",
        description="Read recent messages from the Telegram channel and show what sync would see.",
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--limit", type=int, default=20, help="Number of recent messages to inspect (default 20)")
    args = parser.parse_args(argv)

    env_path = Path(args.env_file).expanduser()
    if env_path.exists():
        load_dotenv(env_path, override=True)
    else:
        load_dotenv()

    api_id_raw = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()
    chat_id_raw = os.getenv("CHAT_ID", "").strip()
    session_str = os.getenv("TG_SESSION_STRING", "").strip()

    if not api_id_raw or not api_hash:
        print("Error: TG_API_ID / TG_API_HASH not set", flush=True)
        return 1
    if not session_str:
        print("Error: TG_SESSION_STRING not set — run 'telecloud sync-auth' first", flush=True)
        return 1

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.utils import pack_bot_file_id
    except ImportError:
        print("Error: telethon not installed — pip install -e .", flush=True)
        return 1

    from backend.sync import parse_caption, _resolve_chat

    client = TelegramClient(StringSession(session_str), int(api_id_raw), api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("Error: session expired — run 'telecloud sync-auth' again", flush=True)
        await client.disconnect()
        return 1

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    me = await client.get_me()
    print(f"\n[debug] logged in as: id={me.id} username=@{me.username} first_name={me.first_name}", flush=True)
    print(f"[debug] CHAT_ID={chat_id_raw}  BOT_TOKEN prefix={bot_token.split(':')[0] if bot_token else 'N/A'}", flush=True)

    # List all dialogs to help identify where the bot is
    print("\n[debug] scanning dialogs for bot...", flush=True)
    bot_user_id = int(bot_token.split(":")[0]) if bot_token else None
    async for dialog in client.iter_dialogs(limit=50):
        entity = dialog.entity
        eid = getattr(entity, "id", None)
        name = getattr(entity, "title", None) or getattr(entity, "first_name", "") or ""
        username = getattr(entity, "username", "") or ""
        is_bot = getattr(entity, "bot", False)
        if is_bot or (bot_user_id and eid == bot_user_id):
            print(f"  [BOT FOUND] id={eid} name={name!r} @{username} unread={dialog.unread_count} total_msgs={dialog.message.id if dialog.message else 0}", flush=True)

    try:
        chat_entity = await _resolve_chat(client, chat_id_raw, bot_token=bot_token)
        eid = getattr(chat_entity, "id", "?")
        ename = getattr(chat_entity, "title", None) or getattr(chat_entity, "first_name", "?")
        print(f"\n=== inspect last {args.limit} messages → entity id={eid} name={ename!r} type={type(chat_entity).__name__} ===\n", flush=True)
    except Exception as exc:
        print(f"Error resolving chat: {exc}", flush=True)
        await client.disconnect()
        return 1

    count = 0
    async for message in client.iter_messages(chat_entity, limit=args.limit):
        count += 1
        caption = message.message or ""
        parsed = parse_caption(caption)
        has_media = message.media is not None

        file_id_from_media = None
        if has_media and parsed is not None:
            try:
                file_id_from_media = pack_bot_file_id(message.media)
            except Exception as e:
                file_id_from_media = f"[pack failed: {e}]"

        print(f"[msg {message.id}]", flush=True)
        print(f"  media     : {type(message.media).__name__ if has_media else 'none'}", flush=True)
        print(f"  caption   : {caption[:120] or '(empty)'}", flush=True)
        print(f"  parse_ok  : {parsed is not None}", flush=True)
        if parsed:
            print(f"  name      : {parsed['name']}", flush=True)
            print(f"  size      : {parsed['size']}", flush=True)
        if file_id_from_media:
            ok = not str(file_id_from_media).startswith("[")
            print(f"  file_id   : {'✓ ok' if ok else file_id_from_media}", flush=True)
        print(flush=True)

    if count == 0:
        print("(no messages found — is CHAT_ID correct?)", flush=True)

    await client.disconnect()
    return 0


async def run_backfill_captions(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="telecloud backfill-captions",
        description="Write sync metadata captions to existing Telegram messages. "
                    "Requires the bot to have 'can_edit_messages' admin permission in the channel.",
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env file. Defaults to .env.")
    args = parser.parse_args(argv)

    env_path = Path(args.env_file).expanduser()
    if env_path.exists():
        load_dotenv(env_path, override=True)
    else:
        load_dotenv()
    os.environ["TELECLOUD_ENV_FILE"] = str(env_path)

    import aiosqlite
    from backend.database import DB_PATH, list_files
    from backend.sync import make_caption
    from backend.telegram import TelegramClient

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    chat_id = os.getenv("CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print("Error: BOT_TOKEN and CHAT_ID must be set in .env", flush=True)
        return 1

    tg = TelegramClient(
        bot_token=bot_token,
        chat_id=chat_id,
        api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
    )

    # Fetch all files across all folders
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files ORDER BY id ASC") as cur:
            records = await cur.fetchall()

    total = len(records)
    ok = 0
    failed = 0
    print(f"telecloud backfill-captions: {total} records to process...", flush=True)

    for row in records:
        record = dict(row)
        try:
            caption = make_caption(
                name=record["name"],
                size=record["size"],
                mime_type=record["mime_type"],
                encrypted=bool(record.get("encrypted", 0)),
                uploaded_at=str(record["uploaded_at"]),
                tg_file_id=record["tg_file_id"],
                tg_thumb_file_id=record.get("tg_thumb_file_id"),
            )
            await tg.edit_message_caption(record["tg_message_id"], caption)
            ok += 1
            print(f"  ✓ [{ok}/{total}] {record['name']}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  ✗ [{record['id']}] {record['name']}: {exc}", flush=True)
        await asyncio.sleep(0.05)  # avoid hitting rate limits

    await tg.aclose()
    print(f"\ntelecloud backfill-captions: done — {ok} ok, {failed} failed", flush=True)
    if failed:
        print("Tip: make sure the bot has 'can_edit_messages' admin permission in the channel.", flush=True)
    return 0 if failed == 0 else 1


async def run_sync_auth(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="telecloud sync-auth",
        description="Authenticate a Telegram user account so the sync feature can read channel history.",
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env file. Defaults to .env.")
    args = parser.parse_args(argv)

    env_path = Path(args.env_file).expanduser()
    if env_path.exists():
        load_dotenv(env_path, override=True)
    else:
        load_dotenv()

    api_id_raw = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()

    if not api_id_raw or not api_hash:
        print("Error: TG_API_ID and TG_API_HASH must be set in .env first.", flush=True)
        print("Add them via the web UI settings → 跨裝置同步, then re-run this command.", flush=True)
        return 1

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("Error: telethon is not installed. Run: pip install -e .", flush=True)
        return 1

    print("telecloud sync-auth: starting user account authentication...", flush=True)
    print("You will be prompted for your phone number and a Telegram OTP.", flush=True)

    client = TelegramClient(StringSession(), int(api_id_raw), api_hash)
    await client.start()  # interactive: prompts phone + OTP in terminal
    session_str = client.session.save()
    await client.disconnect()

    update_env_file(env_path, {"TG_SESSION_STRING": session_str})
    os.environ["TG_SESSION_STRING"] = session_str
    print(f"telecloud sync-auth: session saved to {env_path}", flush=True)
    print("Authentication complete. You can now use the sync button in the web UI.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        if argv is None:
            argv = sys.argv[1:]
        if argv and argv[0] == "sync-auth":
            return asyncio.run(run_sync_auth(argv[1:]))
        if argv and argv[0] == "backfill-captions":
            return asyncio.run(run_backfill_captions(argv[1:]))
        if argv and argv[0] == "inspect-messages":
            return asyncio.run(run_inspect_messages(argv[1:]))
        args = parse_args(argv)
        if args.server:
            return spawn_servers(args)
        return asyncio.run(run_server(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
