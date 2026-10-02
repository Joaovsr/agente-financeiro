from types import SimpleNamespace
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import asyncio
import subprocess
import httpx
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from imageio_ffmpeg import get_ffmpeg_exe
from sqlalchemy import create_engine

from finance_agent.config import Settings
from finance_agent.database import Base
from finance_agent.imports import ExtractedItem, Extraction
import finance_agent.main as main_module
from finance_agent.main import create_app
from finance_agent.models import IncomingEvent
from finance_agent.service import FinanceService, today_local
from sqlalchemy.orm import sessionmaker
from datetime import timedelta


@lru_cache
def sample_voice_ogg() -> bytes:
    result = subprocess.run(
        [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2", "-c:a", "libopus", "-b:a", "16k", "-f", "ogg", "pipe:1"],
        capture_output=True,
        check=True,
    )
    assert result.stdout.startswith(b"OggS")
    return result.stdout


class FakeResponses:
    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(output=[SimpleNamespace(type="function_call", name="create_wallet", arguments='{"name":"Banco","opening_balance":"1000","opened_on":"2026-09-01"}', call_id="call-1")], output_text="")
        return SimpleNamespace(output=[], output_text="Carteira Banco criada com R$ 1.000,00.")

    async def parse(self, **kwargs):
        return SimpleNamespace(output_parsed=Extraction(items=[ExtractedItem(occurred_on=today_local().isoformat(), description="Padaria", amount="35", direction="expense", wallet_name="Banco", category="Alimentação", uncertain=False)]))


class FakeAI:
    def __init__(self):
        self.responses = FakeResponses()
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.transcribe))
        self.transcriptions = 0

    async def transcribe(self, **kwargs):
        self.transcriptions += 1
        assert kwargs["file"][0] == "voice.webm"
        assert kwargs["file"][1].startswith(b"\x1a\x45\xdf\xa3")
        assert kwargs["file"][2] == "audio/webm"
        assert kwargs["extra_body"] == {"languages": ["pt"]}
        assert "language" not in kwargs
        return SimpleNamespace(text="Crie a carteira Banco com saldo inicial de R$ 1.000 em 1/9/2026")

    async def close(self):
        pass


class SlowResponses(FakeResponses):
    async def create(self, **kwargs):
        if self.calls == 0:
            await asyncio.sleep(0.1)
        return await super().create(**kwargs)


class SlowAI(FakeAI):
    def __init__(self):
        super().__init__()
        self.responses = SlowResponses()


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def download(self, voice):
        if hasattr(voice, "file_name"):
            return BytesIO(b"%PDF-1.4 fake")
        return BytesIO(sample_voice_ogg())


class FailOnceBot(FakeBot):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def send_message(self, chat_id, text):
        self.attempts += 1
        if self.attempts == 1:
            raise OSError("simulated Telegram failure")
        await super().send_message(chat_id, text)


class CorruptVoiceBot(FakeBot):
    async def download(self, voice):
        return BytesIO(b"OggSnot-a-real-audio-file")


def update(user_id=123, chat_type="private", update_id=100):
    return {"update_id": update_id, "message": {"message_id": 1, "date": 1782316800, "chat": {"id": user_id, "type": chat_type}, "from": {"id": user_id, "is_bot": False, "first_name": "Dono"}, "text": "Crie a carteira Banco com saldo inicial de R$ 1.000 em 1/9/2026"}}


def test_readiness_reports_database_failure_without_exposing_details(tmp_path):
    database_url = "sqlite:///" + tmp_path.as_posix()
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    with TestClient(create_app(config, bot=FakeBot(), ai_client=FakeAI())) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"detail": "Banco indisponível"}


def test_readiness_requires_current_migrations(tmp_path, monkeypatch):
    database_url = "sqlite:///" + (tmp_path / "unmigrated.db").as_posix()
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    migrations = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    Base.metadata.create_all(create_engine(database_url))
    with TestClient(create_app(config, bot=FakeBot(), ai_client=FakeAI())) as client:
        assert client.get("/readyz").json() == {"detail": "Esquema indisponível"}

    database_url = "sqlite:///" + (tmp_path / "outdated.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    command.upgrade(migrations, "84df5cae7021")
    with TestClient(create_app(config, bot=FakeBot(), ai_client=FakeAI())) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"detail": "Migrações pendentes"}
        command.upgrade(migrations, "head")
        assert client.get("/readyz").json() == {"ok": True}


def test_production_webhook_waits_for_current_schema(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "unmigrated.db").as_posix())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main_module, "make_session_factory", lambda _: factory)
    config = Settings(app_env="production", database_url="postgresql+psycopg://test@localhost/finance", owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        assert client.post("/telegram/webhook", json=update()).status_code == 403
        response = client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}, json=update())
        assert response.status_code == 503
        assert response.json() == {"detail": "Esquema indisponível"}
    assert ai.responses.calls == 0
    assert bot.messages == []


def test_webhook_authorizes_and_deduplicates(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    Base.metadata.create_all(create_engine(database_url))
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot = FakeBot()
    ai = FakeAI()
    app = create_app(config, bot=bot, ai_client=ai)
    with TestClient(app) as client:
        assert client.post("/telegram/webhook", json=update()).status_code == 403
        headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
        assert client.post("/telegram/webhook", headers=headers, json=update(user_id=999)).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=update(chat_type="group")).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=update()).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=update()).status_code == 200
    assert ai.responses.calls == 2
    assert bot.messages == [(123, "Carteira Banco criada com saldo inicial de R$ 1.000,00 em 2026-09-01.")]


def test_voice_is_transcribed_once_for_retried_update(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    Base.metadata.create_all(create_engine(database_url))
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    voice_update = update()
    del voice_update["message"]["text"]
    voice_update["message"]["voice"] = {"file_id": "voice-1", "file_unique_id": "unique-1", "duration": 2, "file_size": 8}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
        assert client.post("/telegram/webhook", headers=headers, json=voice_update).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=voice_update).status_code == 200
    assert ai.transcriptions == 1
    assert len(bot.messages) == 1
    assert bot.messages[0][1].startswith("Entendi: Crie a carteira Banco")


def test_document_creates_review_batch_without_movement(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    service = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    service.create_wallet("Banco", "1000", today_local() - timedelta(days=10), key="wallet")
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    document_update = update(update_id=101)
    del document_update["message"]["text"]
    document_update["message"]["document"] = {"file_id": "doc-1", "file_unique_id": "unique-doc-1", "file_name": "extrato.pdf", "mime_type": "application/pdf", "file_size": 13}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
        assert client.post("/telegram/webhook", headers=headers, json=document_update).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=document_update).status_code == 200
    assert len(bot.messages) == 1
    assert "Nada foi lançado ainda" in bot.messages[0][1]
    assert service.balances()["total"] == "1000.00"


def test_retry_after_telegram_send_failure_reuses_saved_result(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FailOnceBot(), FakeAI()
    app = create_app(config, bot=bot, ai_client=ai)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/telegram/webhook", headers=headers, json=update()).status_code == 500
        assert client.post("/telegram/webhook", headers=headers, json=update()).status_code == 200
    assert bot.attempts == 2
    assert ai.responses.calls == 2
    service = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    assert len(service.list_wallets()) == 1


def test_retry_recovers_committed_write_before_response_was_saved(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = FinanceService(factory, "owner")
    wallet = service.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="setup-wallet")
    with factory.begin() as session:
        session.add(IncomingEvent(owner_id="owner", channel="telegram", event_id="105", user_text="Gastei R$ 35 na padaria hoje"))
    service.record("expense", "35", today_local(), "Padaria", wallet_id=wallet["id"], key="telegram:105:write:1")

    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    request = update(update_id=105)
    request["message"]["text"] = "Gastei R$ 35 na padaria hoje"
    headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        assert client.post("/telegram/webhook", headers=headers, json=request).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=request).status_code == 200
    assert ai.responses.calls == 0
    assert len(bot.messages) == 1
    assert "Recuperei uma operação já gravada" in bot.messages[0][1]
    assert "R$ 35,00" in bot.messages[0][1]
    assert service.balances()["total"] == "965.00"


def test_invalid_document_gets_clear_reply_and_is_not_reprocessed(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    Base.metadata.create_all(create_engine(database_url))
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    document_update = update(update_id=102)
    del document_update["message"]["text"]
    document_update["message"]["document"] = {"file_id": "doc-2", "file_unique_id": "unique-doc-2", "file_name": "extrato.txt", "mime_type": "text/plain", "file_size": 8}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
        assert client.post("/telegram/webhook", headers=headers, json=document_update).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=document_update).status_code == 200
    assert len(bot.messages) == 1
    assert "Envie imagem JPG, PNG, WEBP ou PDF" in bot.messages[0][1]
    assert ai.responses.calls == 0


def test_oversized_voice_gets_clear_reply_without_download(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    Base.metadata.create_all(create_engine(database_url))
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    voice_update = update(update_id=103)
    del voice_update["message"]["text"]
    voice_update["message"]["voice"] = {"file_id": "voice-large", "file_unique_id": "unique-large", "duration": 120, "file_size": 21 * 1024 * 1024}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
        assert client.post("/telegram/webhook", headers=headers, json=voice_update).status_code == 200
    assert bot.messages == [(123, "Áudio acima de 20 MB. Envie uma gravação menor.")]
    assert ai.transcriptions == 0


def test_corrupt_voice_gets_clear_reply_without_model_call(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    Base.metadata.create_all(create_engine(database_url))
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = CorruptVoiceBot(), FakeAI()
    voice_update = update(update_id=104)
    del voice_update["message"]["text"]
    voice_update["message"]["voice"] = {"file_id": "voice-corrupt", "file_unique_id": "unique-corrupt", "duration": 2, "file_size": 24}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
        assert client.post("/telegram/webhook", headers=headers, json=voice_update).status_code == 200
        assert client.post("/telegram/webhook", headers=headers, json=voice_update).status_code == 200
    assert bot.messages == [(123, "Não foi possível converter o áudio de voz")]
    assert ai.transcriptions == 0


def test_concurrent_retries_of_same_update_are_serialized(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    Base.metadata.create_all(create_engine(database_url))
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), SlowAI()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.post, "/telegram/webhook", headers=headers, json=update()) for _ in range(2)]
            assert [future.result().status_code for future in futures] == [200, 200]
    assert ai.responses.calls == 2
    assert len(bot.messages) == 1


def test_webhook_rejects_malformed_and_oversized_updates(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    Base.metadata.create_all(create_engine(database_url))
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
    with TestClient(create_app(config, bot=bot, ai_client=ai)) as client:
        assert client.post("/telegram/webhook", headers=headers, content=b"not-json").status_code == 400
        assert client.post("/telegram/webhook", headers=headers, content=b"x" * (1024 * 1024 + 1)).status_code == 413
    assert bot.messages == []


def test_long_text_is_rejected_before_persisting_event(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    config = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    bot, ai = FakeBot(), FakeAI()
    long_update = update(update_id=106)
    long_update["message"]["text"] = "a" * 10001
    headers = {"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"}
    async def send_twice():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config, bot=bot, ai_client=ai)), base_url="http://test") as client:
            assert (await client.post("/telegram/webhook", headers=headers, json=long_update)).status_code == 200
            assert (await client.post("/telegram/webhook", headers=headers, json=long_update)).status_code == 200

    asyncio.run(send_twice())
    assert bot.messages == [(123, "Mensagem longa demais; divida o pedido.")]
    assert ai.responses.calls == 0
    with sessionmaker(bind=engine)() as session:
        event = session.query(IncomingEvent).filter_by(event_id="106").one()
        assert len(event.user_text) <= 10000
