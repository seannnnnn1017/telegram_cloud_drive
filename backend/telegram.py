import asyncio
from typing import Optional

import httpx


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        api_base_url: str = "https://api.telegram.org",
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        base = api_base_url.rstrip("/")
        self._api = f"{base}/bot{bot_token}"
        self._cdn = f"{base}/file/bot{bot_token}"
        self._chat_id = chat_id
        self._client = http_client or httpx.AsyncClient()
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_document(self, filename: str, content: bytes, mime_type: str) -> dict:
        r, data = await self._post_with_retry(
            f"{self._api}/sendDocument",
            data={"chat_id": self._chat_id},
            files={"document": (filename, content, mime_type)},
            timeout=60.0,
        )
        if not data.get("ok"):
            raise ValueError(f"Telegram error {r.status_code}: {data.get('description', 'unknown error')}")
        msg = data["result"]
        doc = (
            msg.get("document")
            or msg.get("video")
            or msg.get("audio")
            or (msg.get("photo") or [{}])[-1]
        )
        thumbnail = doc.get("thumbnail") or doc.get("thumb") or {}
        return {
            "message_id": msg["message_id"],
            "file_id": doc["file_id"],
            "thumb_file_id": thumbnail.get("file_id"),
        }

    async def _post_with_retry(self, url: str, **kwargs) -> tuple[httpx.Response, dict]:
        last_response = None
        last_data = {}
        for attempt in range(4):
            response = await self._client.post(url, **kwargs)
            data = response.json()
            last_response = response
            last_data = data
            if data.get("ok") or response.status_code not in {429, 500, 502, 503, 504}:
                return response, data

            retry_after = data.get("parameters", {}).get("retry_after")
            delay = float(retry_after) if retry_after is not None else 0.5 * (2 ** attempt)
            await asyncio.sleep(delay)

        return last_response, last_data

    async def get_file_url(self, file_id: str) -> str:
        r = await self._client.post(
            f"{self._api}/getFile",
            json={"file_id": file_id},
            timeout=10.0,
        )
        data = r.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram error {r.status_code}: {data.get('description', 'unknown error')}")
        return f"{self._cdn}/{data['result']['file_path']}"

    async def get_updates(self, offset: Optional[int] = None, timeout: int = 25) -> list[dict]:
        payload = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        r = await self._client.post(f"{self._api}/getUpdates", json=payload, timeout=timeout + 10.0)
        data = r.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram error {r.status_code}: {data.get('description', 'unknown error')}")
        return data["result"]

    async def test_connection(self) -> dict:
        me = await self._client.post(f"{self._api}/getMe", json={}, timeout=10.0)
        me_data = me.json()
        if not me_data.get("ok"):
            raise ValueError(f"Telegram getMe failed: {me_data.get('description', 'unknown error')}")

        chat = await self._client.post(
            f"{self._api}/getChat",
            json={"chat_id": self._chat_id},
            timeout=10.0,
        )
        chat_data = chat.json()
        if not chat_data.get("ok"):
            raise ValueError(f"Telegram getChat failed: {chat_data.get('description', 'unknown error')}")
        return {"bot": me_data["result"], "chat": chat_data["result"]}

    async def copy_message(self, from_chat_id: int | str, message_id: int) -> int:
        r = await self._client.post(
            f"{self._api}/copyMessage",
            json={
                "chat_id": self._chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
            },
            timeout=60.0,
        )
        data = r.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram error {r.status_code}: {data.get('description', 'unknown error')}")
        return data["result"]["message_id"]

    async def download_file(self, file_id: str) -> bytes:
        url = await self.get_file_url(file_id)
        r = await self._client.get(url, timeout=60.0)
        r.raise_for_status()
        return r.content

    async def delete_message(self, message_id: int) -> bool:
        r = await self._client.post(
            f"{self._api}/deleteMessage",
            json={"chat_id": self._chat_id, "message_id": message_id},
            timeout=10.0,
        )
        return r.json().get("ok", False)
