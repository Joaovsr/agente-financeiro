import asyncio
import json

import httpx
import pytest
from aiogram.types import Update

from finance_agent.poller import forward_once


class FakeBot:
    def __init__(self, updates):
        self.updates = updates
        self.offsets = []

    async def get_updates(self, **kwargs):
        self.offsets.append(kwargs["offset"])
        return self.updates


class FakeClient:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.requests = []

    async def post(self, path, *, content, headers):
        self.requests.append((path, json.loads(content), headers))
        return httpx.Response(self.status_code, request=httpx.Request("POST", "http://127.0.0.1:8000" + path))


def test_poller_forwards_telegram_update_with_original_field_aliases():
    update = Update.model_validate({
        "update_id": 42,
        "message": {
            "message_id": 7,
            "date": 1782316800,
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Dono"},
            "text": "Quanto tenho?",
        },
    })
    bot, client = FakeBot([update]), FakeClient()

    assert asyncio.run(forward_once(bot, client, "secret", None)) == 43
    path, body, headers = client.requests[0]
    assert path == "/telegram/webhook"
    assert body["message"]["from"]["id"] == 123
    assert body["message"]["text"] == "Quanto tenho?"
    assert headers["X-Telegram-Bot-Api-Secret-Token"] == "secret"


def test_poller_retries_update_after_api_failure():
    update = Update.model_validate({"update_id": 43})
    bot, client = FakeBot([update]), FakeClient(status_code=503)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(forward_once(bot, client, "secret", 43))
    assert bot.offsets == [43]
