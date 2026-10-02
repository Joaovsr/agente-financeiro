"""Aceitação do domínio em PostgreSQL temporário com migrações aplicadas."""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from finance_agent.database import make_session_factory
from finance_agent.imports import ExtractedItem, Extraction, ImportService
from finance_agent.notifications import NotificationService
from finance_agent.service import DomainError, FinanceService, today_local


def test_postgresql_financial_lifecycle():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Defina TEST_POSTGRES_URL para um PostgreSQL descartável com migrações aplicadas")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").endswith("_test"):
        pytest.fail("TEST_POSTGRES_URL deve apontar para um banco PostgreSQL com nome terminado em _test")

    factory = make_session_factory(url)
    engine = factory.kw["bind"]
    try:
        assert {"wallets", "movements", "schedules", "occurrences", "import_batches", "notifications"} <= set(inspect(engine).get_table_names())
        owner = f"integration-{uuid4().hex}"
        finance = FinanceService(factory, owner)
        other = FinanceService(factory, f"other-{uuid4().hex}")
        today = today_local()
        bank = finance.create_wallet("Banco", "1000", today - timedelta(days=10), key="wallet-bank")
        cash = finance.create_wallet("Dinheiro", "200", today - timedelta(days=10), key="wallet-cash")
        finance.record("expense", "35", today, "Padaria", wallet_id=bank["id"], key="expense")
        finance.record("transfer", "100", today, "Saque", wallet_id=bank["id"], destination_wallet_id=cash["id"], key="transfer")
        assert finance.balances()["total"] == "1165.00"

        investment = finance.create_investment("Reserva", key="investment")
        finance.record("contribution", "200", today, "Aporte", wallet_id=bank["id"], investment_id=investment["id"], key="contribution")
        assert finance.balances()["total"] == "965.00"

        finance.create_schedule("expense", "50", "Conta", today, wallet_id=bank["id"], key="schedule")
        assert finance.projection(today)["projected_total"] == "915.00"
        occurrence = finance.list_occurrences(through=today, status="pending")[0]
        finance.confirm_occurrence(occurrence["id"], "50", today, key="confirm")
        assert finance.balances()["total"] == "915.00"
        assert finance.projection(today)["projected_total"] == "915.00"

        imports = ImportService(finance)
        extracted = Extraction(items=[ExtractedItem(occurred_on=today.isoformat(), description="Mercado", amount="15", direction="expense", wallet_name="Banco", category="Alimentação", uncertain=False)])
        batch = imports.create_batch(b"postgres-test-document", "extrato.pdf", "application/pdf", extracted)
        assert finance.balances()["total"] == "915.00"
        imports.confirm(batch["id"], version=1, selected_ids=[batch["items"][0]["id"]], key="import-confirm")
        assert finance.balances()["total"] == "900.00"
        assert finance.aggregates(today, today)["expense"] == "100.00"

        finance.create_schedule("expense", "10", "Aviso", today + timedelta(days=1), wallet_id=bank["id"], key="reminder-schedule")
        notifications = NotificationService(finance)
        notifications.configure(enabled=True, reminder_days_before=1, negative_alert=False, monthly_summary=False, key="notifications")
        assert any("Aviso" in row["text"] for row in notifications.prepare(on_date=today))

        assert other.list_wallets() == []
        with pytest.raises(DomainError):
            other.balances(wallet_ids=[bank["id"]])
    finally:
        engine.dispose()
