import argparse
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
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
        if current_timeout <= 0:
            await asyncio.sleep(1)
            continue
        await asyncio.sleep(min(1, current_timeout))
        if getattr(app.state, "shutdown_requested", False):
            print("telecloud: shutdown requested from web UI", flush=True)
            server.should_exit = True
            return
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

    config = uvicorn.Config(app, host=bind_host, port=port, log_level=os.getenv("TELECLOUD_LOG_LEVEL", "info"))
    server = uvicorn.Server(config)
    watchdog = asyncio.create_task(idle_watchdog(server, app, idle_timeout))
    if not args.no_browser:
        webbrowser.open(url)
    try:
        await server.serve()
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.server:
        return spawn_servers(args)
    return asyncio.run(run_server(args))


if __name__ == "__main__":
    raise SystemExit(main())
