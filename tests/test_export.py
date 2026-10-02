import json
from datetime import timedelta

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from finance_agent.config import Settings
from finance_agent.database import Base
from finance_agent.manage import export_data
from finance_agent.service import FinanceService, today_local


def test_export_contains_only_authenticated_owner_data(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "finance.db").as_posix()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    owner = FinanceService(factory, "owner")
    other = FinanceService(factory, "other")
    wallet = owner.create_wallet("Banco", "100", today_local() - timedelta(days=1), key="wallet")
    owner.record("expense", "10", today_local(), "Padaria", key="expense", wallet_id=wallet["id"])
    other.create_wallet("Segredo", "999", today_local() - timedelta(days=1), key="wallet")
    settings = Settings(database_url=database_url, owner_id="owner", telegram_user_id=123, telegram_bot_token="123:ABC", telegram_webhook_secret="super-secret-token", openai_api_key="test-key")
    destination = tmp_path / "backup.json"
    export_data(settings, destination)
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert [item["name"] for item in data["wallets"]] == ["Banco"]
    assert len(data["movements"]) == 1
    assert data["movements"][0]["amount"] == "10.00"
    assert data["conversations"] == []
    assert len(data["commands"]) == 2
    with pytest.raises(FileExistsError):
        export_data(settings, destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == data
