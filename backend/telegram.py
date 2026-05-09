import httpx


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._api = f"https://api.telegram.org/bot{bot_token}"
        self._cdn = f"https://api.telegram.org/file/bot{bot_token}"
        self._chat_id = chat_id

    async def send_document(self, filename: str, content: bytes, mime_type: str) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._api}/sendDocument",
                data={"chat_id": self._chat_id},
                files={"document": (filename, content, mime_type)},
                timeout=60.0,
            )
            r.raise_for_status()
            data = r.json()
        if not data["ok"]:
            raise ValueError(data["description"])
        msg = data["result"]
        doc = (
            msg.get("document")
            or msg.get("video")
            or msg.get("audio")
            or (msg.get("photo") or [{}])[-1]
        )
        return {"message_id": msg["message_id"], "file_id": doc["file_id"]}

    async def get_file_url(self, file_id: str) -> str:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._api}/getFile",
                json={"file_id": file_id},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
        if not data["ok"]:
            raise ValueError(data["description"])
        return f"{self._cdn}/{data['result']['file_path']}"

    async def download_file(self, file_id: str) -> bytes:
        url = await self.get_file_url(file_id)
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=60.0)
            r.raise_for_status()
            return r.content

    async def delete_message(self, message_id: int) -> bool:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._api}/deleteMessage",
                json={"chat_id": self._chat_id, "message_id": message_id},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json().get("ok", False)
