from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def test_upgrade_clears_implicit_wallet_default(tmp_path, monkeypatch):
    database_url = str(URL.create("sqlite", database=str(tmp_path / "wallets.db")))
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "426d9bcfa356")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO wallets (id, owner_id, name, opening_balance, opened_on, is_default, active) "
                 "VALUES (:id, :owner_id, :name, :opening_balance, :opened_on, :is_default, :active)"),
            {"id": "old-wallet", "owner_id": "owner", "name": "Banco", "opening_balance": 100, "opened_on": "2026-01-01", "is_default": True, "active": True},
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT is_default FROM wallets WHERE id = 'old-wallet'")) == 0
    engine.dispose()
