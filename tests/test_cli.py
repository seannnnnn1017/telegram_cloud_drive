import backend.cli as cli_module
import asyncio
from backend.cli import (
    browser_host,
    display_host,
    ensure_settings,
    env_needs_setup,
    effective_host,
    find_available_port,
    parse_args,
    resolve_idle_timeout,
    split_server_spec,
    update_env_file,
)


def test_parse_defaults():
    args = parse_args([])
    assert args.env_file == ".env"
    assert args.host == "127.0.0.1"
    assert args.lan is False
    assert args.port == 8000
    assert args.share_base_url is None
    assert args.idle_timeout == 900
    assert args.setup is False
    assert args.no_browser is False


def test_main_handles_keyboard_interrupt(monkeypatch):
    def raise_keyboard_interrupt(_coro):
        _coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.asyncio, "run", raise_keyboard_interrupt)

    assert cli_module.main(["--no-browser"]) == 130


def test_parse_share_base_url():
    args = parse_args(["--share-base-url", "http://localhost:8000"])
    assert args.share_base_url == "http://localhost:8000"


def test_parse_lan_mode():
    args = parse_args(["--lan"])
    assert args.lan is True
    assert effective_host(args) == "0.0.0.0"


def test_display_host_for_lan(monkeypatch):
    monkeypatch.setattr(cli_module, "detect_lan_ip", lambda: "192.168.1.20")
    assert display_host("0.0.0.0", use_lan=True) == "192.168.1.20"


def test_resolve_idle_timeout_from_env(monkeypatch):
    monkeypatch.setenv("TELECLOUD_IDLE_TIMEOUT", "300")
    assert resolve_idle_timeout(900) == 300
    assert resolve_idle_timeout(600) == 600


def test_split_server_spec_with_name():
    assert split_server_spec("work=.env.work") == ("work", ".env.work")


def test_split_server_spec_without_name():
    assert split_server_spec("/tmp/personal.env") == ("personal", "/tmp/personal.env")


def test_browser_host_for_wildcard_bind():
    assert browser_host("0.0.0.0") == "127.0.0.1"
    assert browser_host("127.0.0.1") == "127.0.0.1"


def test_find_available_port_skips_busy_port(monkeypatch):
    attempted_ports = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def setsockopt(self, *args):
            return None

        def bind(self, address):
            attempted_ports.append(address[1])
            if len(attempted_ports) == 1:
                raise OSError("busy")

    monkeypatch.setattr(cli_module.socket, "socket", lambda *args, **kwargs: FakeSocket())
    port = find_available_port("127.0.0.1", 8765)

    assert attempted_ports == [8765, 8766]
    assert port == 8766


async def test_idle_watchdog_honors_shutdown_request():
    class Server:
        should_exit = False

    class State:
        shutdown_requested = True

    class App:
        state = State()

    server = Server()
    await cli_module.idle_watchdog(server, App(), 900)

    assert server.should_exit is True


def test_env_needs_setup_when_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("CHAT_ID", raising=False)

    assert env_needs_setup(tmp_path / ".env") is True


def test_env_needs_setup_when_required_keys_exist(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("CHAT_ID", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("BOT_TOKEN=token\nCHAT_ID=123\n")

    assert env_needs_setup(env_path) is False


def test_update_env_file_preserves_existing_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("VAULT_DB_PATH=vault.db\nBOT_TOKEN=old\n")

    update_env_file(env_path, {"BOT_TOKEN": "new", "CHAT_ID": "123"})

    assert env_path.read_text() == "VAULT_DB_PATH=vault.db\nBOT_TOKEN=new\nCHAT_ID=123\n"


async def test_ensure_settings_captures_chat_id(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    calls = []

    async def fake_telegram_request(api_base_url, bot_token, method, payload):
        calls.append(payload)
        if "offset" not in payload:
            return {"ok": True, "result": [{"update_id": 9}]}
        return {
            "ok": True,
            "result": [
                {
                    "update_id": 10,
                    "message": {"chat": {"id": 456, "first_name": "Ada"}},
                }
            ],
        }

    monkeypatch.setattr(cli_module, "telegram_request", fake_telegram_request)
    monkeypatch.setattr("builtins.input", lambda prompt: "token-from-input")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("CHAT_ID", raising=False)

    await ensure_settings(env_path)

    assert "BOT_TOKEN=token-from-input\n" in env_path.read_text()
    assert "CHAT_ID=456\n" in env_path.read_text()
    assert calls[1]["offset"] == 10
