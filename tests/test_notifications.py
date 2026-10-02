import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from finance_agent.database import Base
from finance_agent.notifications import NotificationService, send_prepared
from finance_agent.service import FinanceService, today_local


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id, text):
        self.sent.append((user_id, text))


class FailOnceBot(FakeBot):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def send_message(self, user_id, text):
        self.attempts += 1
        if self.attempts == 1:
            raise OSError("Falha após possível envio")
        await super().send_message(user_id, text)


def test_reminder_is_configurable_and_sent_once():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=10), key="wallet")
    finance.create_schedule("expense", "100", "Conta", today_local() + timedelta(days=1), key="schedule", wallet_id=wallet["id"])
    notifications = NotificationService(finance)
    bot = FakeBot()
    assert asyncio.run(send_prepared(notifications, bot, 123)) == 0
    notifications.configure(enabled=True, reminder_days_before=1, negative_alert=False, monthly_summary=False, key="settings")
    assert asyncio.run(send_prepared(notifications, bot, 123)) == 1
    assert asyncio.run(send_prepared(notifications, bot, 123)) == 0
    assert len(bot.sent) == 1
    assert "Conta" in bot.sent[0][1]
    assert notifications.list_notifications(status="sent")[0]["attempts"] == 1


def test_same_day_income_offsets_expense_before_negative_alert():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "100", today_local() - timedelta(days=1), key="wallet")
    due = today_local() + timedelta(days=1)
    finance.create_schedule("expense", "200", "Conta", due, key="expense", wallet_id=wallet["id"])
    finance.create_schedule("income", "200", "Entrada", due, key="income", wallet_id=wallet["id"])
    notifications = NotificationService(finance)
    notifications.configure(enabled=True, reminder_days_before=0, negative_alert=True, monthly_summary=False, key="settings")
    assert notifications.prepare() == []


def test_future_negative_balance_produces_alert_with_commitment():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "100", today_local() - timedelta(days=1), key="wallet")
    finance.create_schedule("expense", "200", "Aluguel", today_local() + timedelta(days=2), key="rent", wallet_id=wallet["id"])
    notifications = NotificationService(finance)
    notifications.configure(enabled=True, reminder_days_before=0, negative_alert=True, monthly_summary=False, key="settings")
    prepared = notifications.prepare()
    assert len(prepared) == 1
    assert "Aluguel" in prepared[0]["text"]
    assert "-R$ 100,00" in prepared[0]["text"]


def test_operator_retry_sends_notification_after_original_window():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    day = today_local()
    wallet = finance.create_wallet("Banco", "1000", day - timedelta(days=1), key="wallet")
    finance.create_schedule("expense", "100", "Conta", day, key="schedule", wallet_id=wallet["id"])
    notifications = NotificationService(finance)
    notifications.configure(enabled=True, reminder_days_before=0, negative_alert=False, monthly_summary=False, key="settings")
    bot = FailOnceBot()
    with pytest.raises(OSError):
        asyncio.run(send_prepared(notifications, bot, 123, on_date=day))
    uncertain = notifications.list_notifications(status="uncertain")
    assert len(uncertain) == 1
    notification_id = uncertain[0]["id"]
    assert notifications.retry_uncertain(notification_id)
    assert len(notifications.list_notifications(status="retry_pending")) == 1
    assert asyncio.run(send_prepared(notifications, bot, 123, on_date=day + timedelta(days=2))) == 1
    assert notifications.list_notifications(status="sent")[0]["attempts"] == 2
    assert bot.sent == [(123, "Lembrete: Conta de R$ 100,00 em " + day.isoformat() + ". Confirme quando ocorrer.")]


def test_operator_can_review_and_retry_stuck_sending_state():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    day = today_local()
    wallet = finance.create_wallet("Banco", "1000", day - timedelta(days=1), key="wallet")
    finance.create_schedule("expense", "100", "Conta", day, key="schedule", wallet_id=wallet["id"])
    notifications = NotificationService(finance)
    notifications.configure(enabled=True, reminder_days_before=0, negative_alert=False, monthly_summary=False, key="settings")
    pending = notifications.prepare(on_date=day)
    assert notifications.mark_sending(pending[0]["id"])
    assert notifications.retry_uncertain(pending[0]["id"])
    assert notifications.list_notifications(status="retry_pending")[0]["id"] == pending[0]["id"]
