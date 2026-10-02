import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from finance_agent import manage
from finance_agent.config import Settings
from finance_agent.database import Base


def test_discovery_lists_only_private_sender_ids(monkeypatch):
    class FakeBot:
        def __init__(self, token):
            assert token == "123:ABC"
            self.session = SimpleNamespace(close=self.close)

        async def close(self):
            pass

        async def get_updates(self, **kwargs):
            private = SimpleNamespace(chat=SimpleNamespace(type="private"), from_user=SimpleNamespace(id=123))
            group = SimpleNamespace(chat=SimpleNamespace(type="group"), from_user=SimpleNamespace(id=999))
            return [SimpleNamespace(message=private), SimpleNamespace(message=private), SimpleNamespace(message=group)]

    monkeypatch.setattr(manage, "Bot", FakeBot)
    assert asyncio.run(manage.discover_private_user_ids("123:ABC")) == [123]


def test_preflight_requires_production_and_current_schema(tmp_path, monkeypatch):
    database_url = "sqlite:///" + (tmp_path / "preflight.db").as_posix()
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(manage, "make_session_factory", lambda _: factory)
    config = Settings(database_url="postgresql+psycopg://test@localhost/finance", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="a-secret-long-enough", openai_api_key="sk-example")
    with pytest.raises(ValueError, match="APP_ENV=production"):
        manage.preflight(config)

    config.app_env = "production"
    Base.metadata.create_all(engine)
    with pytest.raises(RuntimeError, match="Esquema indisponível"):
        manage.preflight(config)

    engine.dispose()
    database_url = "sqlite:///" + (tmp_path / "migrated.db").as_posix()
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    manage.preflight(config)
    engine.dispose()
