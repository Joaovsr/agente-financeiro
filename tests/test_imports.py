from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from finance_agent.database import Base
from finance_agent.agent import FinancialAgent
from finance_agent.imports import ExtractedItem, Extraction, ImportService
from finance_agent.service import DomainError, FinanceService, today_local


@pytest.fixture
def services():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=10), key="wallet")
    return finance, ImportService(finance), wallet


def extracted(amount="35", direction="expense", occurred_on=None):
    return Extraction(items=[ExtractedItem(occurred_on=(occurred_on or today_local()).isoformat(), description="Padaria", amount=amount, direction=direction, wallet_name="Banco", category="Alimentação", uncertain=False)])


def test_import_requires_review_and_confirmation(services):
    finance, imports, wallet = services
    batch = imports.create_batch(b"documento-1", "extrato.pdf", "application/pdf", extracted())
    assert finance.balances()["total"] == "1000.00"
    assert imports.create_batch(b"documento-1", "extrato.pdf", "application/pdf", extracted())["id"] == batch["id"]
    result = imports.confirm(batch["id"], version=1, selected_ids=[batch["items"][0]["id"]], key="confirm-1")
    assert result["status"] == "confirmed"
    assert finance.balances()["total"] == "965.00"
    assert imports.confirm(batch["id"], version=1, selected_ids=[batch["items"][0]["id"]], key="confirm-1") == result


def test_import_detects_existing_movement_and_blocks_silent_duplicate(services):
    finance, imports, wallet = services
    finance.record("expense", "35", today_local(), "Padaria", key="record-1", wallet_id=wallet["id"])
    batch = imports.create_batch(b"documento-2", "extrato.pdf", "application/pdf", extracted())
    assert batch["items"][0]["duplicate_movement_ids"]
    with pytest.raises(DomainError, match="duplicado"):
        imports.confirm(batch["id"], version=1, selected_ids=[batch["items"][0]["id"]], key="confirm-1")
    assert finance.balances()["total"] == "965.00"


def test_import_can_link_pending_occurrence(services):
    finance, imports, wallet = services
    due = today_local()
    finance.create_schedule("expense", "35", "Padaria", due, key="schedule-1", wallet_id=wallet["id"])
    batch = imports.create_batch(b"documento-3", "extrato.pdf", "application/pdf", extracted())
    item = batch["items"][0]
    assert item["matching_occurrence_ids"]
    with pytest.raises(DomainError, match="programação"):
        imports.confirm(batch["id"], version=1, selected_ids=[item["id"]], key="confirm-1")
    changed = imports.edit_item(batch["id"], item["id"], version=1, patch={"action": "link", "occurrence_id": item["matching_occurrence_ids"][0]}, key="edit-1")
    confirmed = imports.confirm(batch["id"], version=changed["version"], selected_ids=[item["id"]], key="confirm-2")
    assert confirmed["result"]["created"][0]["occurrence_id"] == item["matching_occurrence_ids"][0]
    assert finance.projection(today_local())["projected_total"] == "965.00"


def test_import_can_change_linked_draft_to_separate_movement(services):
    finance, imports, wallet = services
    finance.create_schedule("expense", "35", "Padaria", today_local(), key="schedule-separate", wallet_id=wallet["id"])
    batch = imports.create_batch(b"documento-desvincular", "extrato.pdf", "application/pdf", extracted())
    item = batch["items"][0]
    linked = imports.edit_item(batch["id"], item["id"], version=1, patch={"action": "link", "occurrence_id": item["matching_occurrence_ids"][0]}, key="link-draft")
    separate = imports.edit_item(batch["id"], item["id"], version=linked["version"], patch={"action": "include", "allow_unlinked": True}, key="separate-draft")
    assert separate["items"][0]["occurrence_id"] is None
    confirmed = imports.confirm(batch["id"], version=separate["version"], selected_ids=[item["id"]], key="confirm-separate")
    assert confirmed["result"]["created"][0]["occurrence_id"] is None
    assert finance.balances()["total"] == "965.00"
    assert finance.projection(today_local())["projected_total"] == "930.00"


def test_agent_requires_explicit_matching_selection_for_confirmation(services):
    finance, imports, wallet = services
    batch = imports.create_batch(b"documento-4", "extrato.pdf", "application/pdf", extracted())
    agent = FinancialAgent(finance, model="test", client=object())
    args = {"batch_id": batch["id"], "version": 1, "selected_ids": [batch["items"][0]["id"]]}
    with pytest.raises(DomainError, match="explicitamente"):
        agent._validate_user_approval("confirm_import_batch", args, "Quanto tenho?")
    with pytest.raises(DomainError, match="não corresponde"):
        agent._validate_user_approval("confirm_import_batch", args, "Confirme item 2")
    agent._validate_user_approval("confirm_import_batch", args, "Confirme item 1")


def test_agent_edits_only_item_referenced_by_owner(services):
    finance, imports, _ = services
    entries = extracted().items
    entries.append(ExtractedItem(occurred_on=today_local().isoformat(), description="Mercado", amount="12", direction="expense", wallet_name="Banco", category="Alimentação", uncertain=False))
    batch = imports.create_batch(b"documento-dois-itens", "extrato.pdf", "application/pdf", Extraction(items=entries))
    first, second = batch["items"]
    agent = FinancialAgent(finance, model="test", client=object())
    args = {"batch_id": batch["id"], "item_id": first["id"], "amount": None}

    with pytest.raises(DomainError, match="número ou ID do item"):
        agent._validate_user_approval("edit_import_item", args, "Ignore item 2")
    with pytest.raises(DomainError, match="número ou ID do item"):
        agent._validate_user_approval("edit_import_item", args, "Ignore a padaria")
    agent._validate_user_approval("edit_import_item", {**args, "item_id": second["id"]}, "Ignore item 2")

    with pytest.raises(DomainError, match="valor em reais informado"):
        agent._validate_user_approval("edit_import_item", {**args, "amount": "300"}, "Corrija item 1 para R$ 30")
    agent._validate_user_approval("edit_import_item", {**args, "amount": "30"}, "Corrija item 1 para R$ 30")


def test_agent_requires_batch_id_when_multiple_drafts_need_edit(services):
    finance, imports, _ = services
    first = imports.create_batch(b"primeiro-lote", "a.pdf", "application/pdf", extracted())
    imports.create_batch(b"segundo-lote", "b.pdf", "application/pdf", extracted())
    agent = FinancialAgent(finance, model="test", client=object())
    args = {"batch_id": first["id"], "item_id": first["items"][0]["id"], "amount": None}
    with pytest.raises(DomainError, match="mais de um lote"):
        agent._validate_user_approval("edit_import_item", args, "Ignore item 1")
    agent._validate_user_approval("edit_import_item", args, f"Ignore item 1 do lote {first['id']}")


def test_import_suggests_link_when_estimated_amount_differs(services):
    finance, imports, wallet = services
    finance.create_schedule("expense", "40", "Padaria", today_local(), key="schedule-1", wallet_id=wallet["id"], estimated=True)
    batch = imports.create_batch(b"documento-5", "extrato.pdf", "application/pdf", extracted(amount="35"))
    assert batch["items"][0]["matching_occurrence_ids"]


def test_import_requires_category_correction_before_confirming(services):
    finance, imports, _ = services
    data = extracted()
    data.items[0].category = "x" * 101
    batch = imports.create_batch(b"documento-categoria-longa", "extrato.pdf", "application/pdf", data)
    item = batch["items"][0]
    with pytest.raises(DomainError, match="Categoria deve ter no máximo 100"):
        imports.confirm(batch["id"], version=1, selected_ids=[item["id"]], key="confirm-invalid")
    assert finance.list_movements() == []
    changed = imports.edit_item(batch["id"], item["id"], version=1, patch={"category": "Alimentação"}, key="edit-category")
    imports.confirm(batch["id"], version=changed["version"], selected_ids=[item["id"]], key="confirm-valid")
    assert finance.list_movements()[0]["category"] == "Alimentação"
