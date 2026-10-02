from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from finance_agent.database import Base
from finance_agent.service import DomainError, FinanceService, money, today_local


@pytest.fixture
def service():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return FinanceService(factory, "owner-1")


def wallet(service, name="Banco", opening="1000.00", key="wallet-1"):
    return service.create_wallet(name, opening, today_local() - timedelta(days=10), key=key)


def test_money_rejects_float_artifacts_and_invalid_values():
    assert money("12.30") == Decimal("12.30")
    for value in ("0", "-1", "NaN", "1.234"):
        with pytest.raises(DomainError):
            money(value)


def test_balances_transfer_and_idempotency(service):
    a = wallet(service)
    b = wallet(service, "Dinheiro", "50", "wallet-2")
    service.record("expense", "35", today_local(), "Padaria", key="message-1", wallet_id=a["id"])
    transfer = service.record("transfer", "100", today_local(), "Saque", key="message-2", wallet_id=a["id"], destination_wallet_id=b["id"])
    assert service.record("transfer", "100", today_local(), "Saque", key="message-2", wallet_id=a["id"], destination_wallet_id=b["id"]) == transfer
    result = service.balances()
    by_name = {item["name"]: item["balance"] for item in result["wallets"]}
    assert by_name == {"Banco": "865.00", "Dinheiro": "150.00"}
    assert result["total"] == "1015.00"
    assert service.balances(wallet_ids=[a["id"]])["total"] == "865.00"


def test_multiple_wallets_require_choice_until_owner_sets_default(service):
    bank = wallet(service)
    cash = wallet(service, "Dinheiro", "50", "wallet-2")
    assert bank["is_default"] is False
    with pytest.raises(DomainError, match="Informe a carteira"):
        service.record("expense", "35", today_local(), "Padaria", key="expense-1")
    assert service.balances()["total"] == "1050.00"

    choice = service.set_default_wallet(cash["id"], key="choose-cash")
    assert service.set_default_wallet(cash["id"], key="choose-cash") == choice
    expense = service.record("expense", "35", today_local(), "Padaria", key="expense-1")
    assert expense["wallet_id"] == cash["id"]
    assert service.balances()["total"] == "1015.00"

    service.set_default_wallet(None, key="clear-default")
    with pytest.raises(DomainError, match="Informe a carteira"):
        service.record("expense", "10", today_local(), "Outro gasto", key="expense-2")
    with pytest.raises(DomainError, match="Carteira não encontrada"):
        FinanceService(service.factory, "owner-2").set_default_wallet(bank["id"], key="other-owner")


def test_confirmed_schedule_moves_amount_from_projection_to_balance(service):
    a = wallet(service)
    due = today_local() + timedelta(days=1)
    schedule = service.create_schedule("income", "5000", "Salário", due, key="schedule-1", wallet_id=a["id"])
    before = service.projection(due)
    assert before["current"]["total"] == "1000.00"
    assert before["projected_total"] == "6000.00"
    occurrence = next(o for o in service.list_occurrences(through=due) if o["schedule_id"] == schedule["id"])
    service.confirm_occurrence(occurrence["id"], "4900", today_local(), key="confirm-1")
    after = service.projection(due)
    assert after["current"]["total"] == "5900.00"
    assert after["projected_total"] == "5900.00"
    assert len(service.list_occurrences(through=due, status="confirmed")) == 1


def test_overdue_occurrence_is_not_silently_counted(service):
    a = wallet(service)
    yesterday = today_local() - timedelta(days=1)
    service.create_schedule("expense", "200", "Conta", yesterday, key="schedule-1", wallet_id=a["id"])
    projection = service.projection(today_local() + timedelta(days=1))
    assert projection["projected_total"] == "1000.00"
    assert len(projection["overdue_excluded"]) == 1


def test_owner_cannot_access_another_owners_wallet(service):
    a = wallet(service)
    other = FinanceService(service.factory, "owner-2")
    with pytest.raises(DomainError):
        other.record("expense", "1", today_local(), "Teste", key="message-1", wallet_id=a["id"])


def test_realized_movement_cannot_be_future_dated(service):
    a = wallet(service)
    with pytest.raises(DomainError):
        service.record("income", "100", today_local() + timedelta(days=1), "Salário", key="message-1", wallet_id=a["id"])


def test_correction_preserves_history_and_recalculates_balance(service):
    a = wallet(service)
    original = service.record("expense", "35", today_local(), "Padaria", key="message-1", wallet_id=a["id"])
    corrected = service.correct_movement(original["id"], key="message-2", amount="30")
    assert corrected["previous"]["amount"] == "35.00"
    assert corrected["current"]["amount"] == "30.00"
    history = service.movement_history(corrected["current"]["id"])
    assert [row["amount"] for row in history] == ["35.00", "30.00"]
    assert history[0]["voided_at"] is not None
    assert service.balances()["total"] == "970.00"
    service.void_movement(corrected["current"]["id"], key="message-3")
    assert service.balances()["total"] == "1000.00"


def test_same_idempotency_key_with_different_arguments_is_rejected(service):
    a = wallet(service)
    service.record("expense", "35", today_local(), "Padaria", key="message-1", wallet_id=a["id"])
    with pytest.raises(DomainError, match="comandos diferentes"):
        service.record("expense", "50", today_local(), "Padaria", key="message-1", wallet_id=a["id"])


def test_manual_entry_must_resolve_matching_schedule(service):
    a = wallet(service)
    service.create_schedule("income", "5000", "Salário", today_local(), key="schedule-1", wallet_id=a["id"])
    with pytest.raises(DomainError, match="Possível programação"):
        service.record("income", "5000", today_local(), "Salário", key="message-1", wallet_id=a["id"])
    separate = service.record("income", "5000", today_local(), "Outro recebimento", key="message-2", wallet_id=a["id"], allow_unlinked=True)
    assert separate["amount"] == "5000.00"


def test_estimated_schedule_still_requires_link_when_real_amount_differs(service):
    a = wallet(service)
    service.create_schedule("income", "5000", "Salário", today_local(), key="schedule-1", wallet_id=a["id"], estimated=True)
    with pytest.raises(DomainError, match="Possível programação"):
        service.record("income", "4900", today_local(), "Salário líquido", key="message-1", wallet_id=a["id"])


def test_cancel_schedule_removes_pending_projection(service):
    a = wallet(service)
    due = today_local() + timedelta(days=1)
    schedule = service.create_schedule("expense", "100", "Conta", due, key="schedule-1", wallet_id=a["id"])
    assert service.projection(due)["projected_total"] == "900.00"
    service.cancel_schedule(schedule["id"], key="cancel-1")
    assert service.projection(due)["projected_total"] == "1000.00"


def test_edit_schedule_replaces_future_occurrences(service):
    a = wallet(service)
    due = today_local() + timedelta(days=1)
    old = service.create_schedule("expense", "100", "Conta", due, key="schedule-1", wallet_id=a["id"])
    assert service.projection(due)["projected_total"] == "900.00"
    changed = service.edit_schedule(old["id"], key="edit-1", effective_on=due, amount="120", description="Conta corrigida", wallet_id=a["id"], frequency="once", day_of_month=None, estimated=False)
    assert changed["new_schedule_id"] != old["id"]
    assert service.projection(due)["projected_total"] == "880.00"


def test_edit_schedule_keeps_unmaterialized_overdue_occurrence(service):
    account = wallet(service)
    overdue = today_local() - timedelta(days=1)
    effective = today_local() + timedelta(days=1)
    old = service.create_schedule("expense", "100", "Conta antiga", overdue, key="old-schedule", wallet_id=account["id"])

    service.edit_schedule(old["id"], key="edit-schedule", effective_on=effective, amount="120", description="Conta nova", wallet_id=account["id"], frequency="once", day_of_month=None, estimated=False)

    previous = [item for item in service.list_occurrences(through=effective) if item["schedule_id"] == old["id"]]
    assert len(previous) == 1
    assert previous[0]["due_on"] == overdue.isoformat()
    assert previous[0]["status"] == "pending"
    forecast = service.projection(effective)
    assert [item["id"] for item in forecast["overdue_excluded"]] == [previous[0]["id"]]
    assert forecast["projected_total"] == "880.00"


def test_aggregates_exclude_transfer_and_simulation_does_not_write(service):
    a = wallet(service)
    b = wallet(service, "Dinheiro", "0", "wallet-2")
    service.record("expense", "35", today_local(), "Padaria", key="expense-1", wallet_id=a["id"], category="Alimentação")
    service.record("transfer", "100", today_local(), "Saque", key="transfer-1", wallet_id=a["id"], destination_wallet_id=b["id"])
    summary = service.aggregates(today_local(), today_local())
    assert summary["expense"] == "35.00"
    assert summary["expense_categories"] == {"Alimentação": "35.00"}
    simulated = service.simulate_expense("50", today_local(), wallet_id=a["id"])
    assert simulated["persisted"] is False
    assert service.balances()["total"] == "965.00"


def test_investment_flows_are_separate_from_consumption(service):
    wallet_row = wallet(service)
    investment = service.create_investment("Reserva", key="investment")
    service.record("contribution", "200", today_local(), "Aporte", key="contribution", wallet_id=wallet_row["id"], investment_id=investment["id"])
    service.record("withdrawal", "50", today_local(), "Resgate", key="withdrawal", wallet_id=wallet_row["id"], investment_id=investment["id"])
    service.value_investment(investment["id"], "180", today_local(), key="valuation")
    assert service.balances()["total"] == "850.00"
    assert service.aggregates(today_local(), today_local())["expense"] == "0.00"
    tracked = service.list_investments()[0]
    assert tracked["net_contributed"] == "150.00"
    assert tracked["current_value"] == "180.00"


def test_investment_value_cannot_be_recorded_as_future_actual(service):
    investment = service.create_investment("Reserva", key="investment")
    with pytest.raises(DomainError, match="data futura"):
        service.value_investment(investment["id"], "200", today_local() + timedelta(days=1), key="future-value")
    assert service.list_investments()[0]["current_value"] is None


def test_financial_text_is_validated_before_persistence(service):
    account = wallet(service)
    with pytest.raises(DomainError, match="Descrição deve ter no máximo 300"):
        service.record("expense", "10", today_local(), "x" * 301, key="long-description", wallet_id=account["id"])
    with pytest.raises(DomainError, match="Categoria deve ter no máximo 100"):
        service.record("expense", "10", today_local(), "Padaria", key="long-category", wallet_id=account["id"], category="x" * 101)
    assert service.list_movements() == []


def test_reconciliation_adjusts_balance_without_creating_expense(service):
    wallet_row = wallet(service)
    adjustment = service.reconcile_wallet(wallet_row["id"], "950", today_local(), key="reconcile-1")
    assert adjustment["difference"] == "-50.00"
    assert service.balances()["total"] == "950.00"
    assert service.aggregates(today_local(), today_local())["expense"] == "0.00"
    assert service.reconcile_wallet(wallet_row["id"], "950", today_local(), key="reconcile-1") == adjustment
    assert service.balances()["total"] == "950.00"


def test_monthly_day_31_uses_last_day_in_february(service):
    from datetime import date

    wallet_row = service.create_wallet("Banco antigo", "0", date(2026, 1, 1), key="old-wallet")
    schedule = service.create_schedule("expense", "100", "Conta", date(2026, 9, 30), key="schedule-31", wallet_id=wallet_row["id"], frequency="monthly", day_of_month=31)
    occurrences = service.list_occurrences(through=date(2027, 3, 31))
    due_dates = [o["due_on"] for o in occurrences if o["schedule_id"] == schedule["id"]]
    assert "2027-02-28" in due_dates


def test_monthly_day_zero_is_rejected_on_create_and_edit(service):
    account = wallet(service)
    tomorrow = today_local() + timedelta(days=1)
    with pytest.raises(DomainError, match="Dia do mês inválido"):
        service.create_schedule("expense", "100", "Conta", tomorrow, key="invalid-create", wallet_id=account["id"], frequency="monthly", day_of_month=0)
    schedule = service.create_schedule("expense", "100", "Conta", tomorrow, key="valid-create", wallet_id=account["id"], frequency="monthly", day_of_month=1)
    with pytest.raises(DomainError, match="Dia do mês inválido"):
        service.edit_schedule(schedule["id"], key="invalid-edit", effective_on=tomorrow, amount="100", description="Conta", wallet_id=account["id"], frequency="monthly", day_of_month=0, estimated=False)
    schedules = service.list_schedules()
    assert len(schedules) == 1 and schedules[0]["active"] is True
