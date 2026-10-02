import asyncio
import calendar
import json
import time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from finance_agent.agent import FinancialAgent
from finance_agent.database import Base
from finance_agent.evals import evaluate, example_statement_pdf
from finance_agent.service import FinanceService, today_local


class ScriptedResponses:
    def __init__(self, calls):
        self.script = list(calls)
        self.seen_outputs = []
        self.offered_tools = []

    async def create(self, **kwargs):
        self.seen_outputs.extend(item for item in kwargs["input"] if isinstance(item, dict) and item.get("type") == "function_call_output")
        self.offered_tools.append({item["name"] for item in kwargs["tools"]})
        next_response = self.script.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return next_response


class ScriptedClient:
    def __init__(self, calls):
        self.responses = ScriptedResponses(calls)


def response_for(name, args):
    return SimpleNamespace(output=[SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id="call-1")], output_text="")


def final(text):
    return SimpleNamespace(output=[], output_text=text)


def test_onboarding_guides_first_wallet_and_short_balance_reply():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([]))
    welcome = asyncio.run(agent.reply("/start", event_key="welcome"))
    assert "Tenho saldo inicial de 700 no Banco" in welcome
    setup = asyncio.run(agent.reply("Faça meu onboarding quero preencher os dados iniciais", event_key="setup"))
    assert "saldo inicial" in setup
    history = [{"role": "user", "content": "Faça meu onboarding quero preencher os dados iniciais"}, {"role": "assistant", "content": setup}]
    followup = asyncio.run(agent.reply("700", event_key="balance", history=history))
    assert "R$ 700,00" in followup and "nome da conta" in followup
    assert finance.list_wallets() == []

    finance.create_wallet("Banco", "700", today_local(), key="opening")
    ready = asyncio.run(agent.reply("/start", event_key="ready"))
    assert "já está cadastrada" in ready


def test_onboarding_accepts_one_natural_balance_per_message():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([]))
    history = [{"role": "user", "content": "/start"}, {"role": "assistant", "content": "Envie uma conta por mensagem."}]
    multiple = asyncio.run(agent.reply("Tenho saldo inicial de 699,86 no mercado livre e 287 no bybit", event_key="multiple", history=history))
    assert "mais de um saldo" in multiple
    assert finance.list_wallets() == []
    answer = asyncio.run(agent.reply("Tenho saldo inicial de 699,86 no mercado livre", event_key="single", history=history))
    assert "Carteira mercado livre criada" in answer and "R$ 699,86" in answer
    retry = asyncio.run(agent.reply("Tenho saldo inicial de 699,86 no mercado livre", event_key="single", history=history))
    assert retry == answer
    assert len(finance.list_wallets()) == 1
    assert finance.balances()["total"] == "699.86"


def test_expense_with_multiple_wallets_asks_for_wallet_and_accepts_followup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    bank = finance.create_wallet("Mercado Livre", "700", today_local(), key="bank")
    finance.create_wallet("Bybit", "287", today_local(), key="bybit")
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([]))
    first = "Adicione um gasto de 55 de BlaBlaCar"
    question = asyncio.run(agent.reply(first, event_key="expense-question"))
    assert "Qual carteira" in question and "Mercado Livre" in question and "Bybit" in question
    split = asyncio.run(agent.reply("Vou pagar hoje 55 reais + 15 de Uber", event_key="two-expenses"))
    assert "cada lançamento" in split
    args = {"kind": "expense", "amount": "55", "occurred_on": today_local().isoformat(), "description": "BlaBlaCar", "wallet_id": bank["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Transporte", "allow_unlinked": False}
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([response_for("record_movement", args), final("Registrado.")]))
    history = [{"role": "user", "content": first}, {"role": "assistant", "content": question}]
    answer = asyncio.run(agent.reply("Mercado Livre", event_key="expense-followup", history=history))
    assert "Movimento registrado: R$ 55,00" in answer
    assert finance.balances()["total"] == "932.00"


def test_transfer_imperative_is_authorized_with_both_wallets_named():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    bank = finance.create_wallet("Banco", "1000", today_local(), key="bank")
    cash = finance.create_wallet("Dinheiro", "200", today_local(), key="cash")
    args = {"kind": "transfer", "amount": "50", "occurred_on": today_local().isoformat(), "description": "Transferência entre carteiras", "wallet_id": bank["id"], "destination_wallet_id": cash["id"], "investment_id": None, "occurrence_id": None, "category": None, "allow_unlinked": False}
    client = ScriptedClient([response_for("record_movement", args), final("Transferência registrada.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Transfira R$ 50 da carteira Banco para a carteira Dinheiro hoje.", event_key="transfer"))
    assert "transferência de R$ 50,00 de Banco para Dinheiro" in answer
    assert finance.balances()["total"] == "1200.00"


def test_confirmations_describe_financial_result_instead_of_generic_status():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local(), key="bank")
    investment = finance.create_investment("Tesouro", key="investment")
    valuation = finance.value_investment(investment["id"], "150", today_local(), key="valuation")
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([]))
    assert "Investimento Tesouro criado" in agent.describe_committed_write("create_investment", investment)
    assert "Tesouro: R$ 150,00" in agent.describe_committed_write("value_investment", valuation)
    adjustment = finance.reconcile_wallet(wallet["id"], "900", today_local(), key="adjustment")
    assert "Banco ajustado de R$ 1.000,00 para R$ 900,00" in agent.describe_committed_write("reconcile_wallet", adjustment)
    tomorrow = today_local() + timedelta(days=1)
    schedule = finance.create_schedule("expense", "40", "Academia", tomorrow, wallet_id=wallet["id"], key="schedule")
    edited = finance.edit_schedule(schedule["id"], key="edit-schedule", effective_on=tomorrow, amount="45", description="Academia", wallet_id=wallet["id"], frequency="monthly", day_of_month=tomorrow.day, estimated=False)
    assert "Academia atualizada para R$ 45,00" in agent.describe_committed_write("edit_schedule", edited)
    cancelled = finance.cancel_schedule(edited["new_schedule_id"], key="cancel-schedule")
    assert "Programação cancelada" in agent.describe_committed_write("cancel_schedule", cancelled)


def test_agent_executes_server_tool_and_reuses_idempotent_result():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    args = {"kind": "expense", "amount": "35", "occurred_on": today_local().isoformat(), "description": "Padaria", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    client = ScriptedClient([response_for("record_movement", args), final("Registrei R$ 35 na padaria.")])
    agent = FinancialAgent(finance, model="test", client=client)
    answer = asyncio.run(agent.reply("Gastei R$ 35 na padaria hoje", event_key="telegram:1"))
    assert "Movimento registrado: R$ 35,00" in answer
    assert "carteira Banco" in answer
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is True
    assert finance.balances()["total"] == "965.00"
    retry = FinancialAgent(finance, model="test", client=ScriptedClient([response_for("record_movement", args), final("Já registrado.")]))
    asyncio.run(retry.reply("Gastei R$ 35 na padaria hoje", event_key="telegram:1"))
    assert finance.balances()["total"] == "965.00"


def test_confirmed_write_reply_uses_server_results_for_multiple_movements():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    first = {"kind": "expense", "amount": "10", "occurred_on": today_local().isoformat(), "description": "Café", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    second = {**first, "description": "Almoço"}
    client = ScriptedClient([response_for("record_movement", first), response_for("record_movement", second), final("Registrei R$ 999 e o saldo é R$ 1.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Gastei R$ 10 em café e R$ 10 no almoço hoje", event_key="telegram:multi"))
    assert answer.count("R$ 10,00") == 2
    assert "R$ 999" not in answer and "R$ 1." not in answer
    assert finance.balances()["total"] == "980.00"


def test_distinct_amounts_in_one_message_require_separate_writes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    args = {"kind": "expense", "amount": "5000", "occurred_on": today_local().isoformat(), "description": "Padaria", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    client = ScriptedClient([response_for("record_movement", args), final("Envie cada lançamento separadamente.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Gastei R$ 35 na padaria e recebi R$ 5.000 de salário", event_key="different-amounts"))
    result = json.loads(client.responses.seen_outputs[0]["output"])
    assert result["ok"] is False
    assert "mais de um valor" in result["error"]
    assert answer == "Envie cada lançamento separadamente."
    assert finance.list_movements() == []


def test_agent_rejects_wallet_guessed_from_multiple_choices():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    bank = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="bank")
    cash = finance.create_wallet("Dinheiro", "50", today_local() - timedelta(days=1), key="cash")
    args = {"kind": "expense", "amount": "35", "occurred_on": today_local().isoformat(), "description": "Padaria", "wallet_id": bank["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    client = ScriptedClient([response_for("record_movement", args), final("Qual carteira devo usar?")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Gastei R$ 35 na padaria hoje", event_key="ambiguous-wallet"))
    assert "Qual carteira" in answer
    assert client.responses.seen_outputs == []
    assert finance.balances()["total"] == "1050.00"

    finance.set_default_wallet(bank["id"], key="bank-default")
    client = ScriptedClient([response_for("record_movement", args), final("Qual carteira devo usar?")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Gastei R$ 35 em Dinheiro hoje", event_key="wrong-wallet"))
    result = json.loads(client.responses.seen_outputs[0]["output"])
    assert result["ok"] is False
    assert finance.balances()["total"] == "1050.00"

    args["wallet_id"] = cash["id"]
    client = ScriptedClient([response_for("record_movement", args), final("Registrei R$ 35 em Dinheiro.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Gastei R$ 35 em Dinheiro hoje", event_key="selected-wallet"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is True
    assert finance.balances()["total"] == "1015.00"


def test_agent_changes_default_only_on_explicit_named_request():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    bank = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="bank")
    cash = finance.create_wallet("Dinheiro", "50", today_local() - timedelta(days=1), key="cash")
    client = ScriptedClient([response_for("set_default_wallet", {"wallet_id": cash["id"]}), final("Dinheiro é a carteira padrão.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Defina Dinheiro como carteira padrão", event_key="set-default"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is True
    assert [item["name"] for item in finance.list_wallets() if item["is_default"]] == ["Dinheiro"]

    client = ScriptedClient([response_for("set_default_wallet", {"wallet_id": bank["id"]}), final("Qual carteira será a padrão?")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Quero mudar a carteira padrão", event_key="wrong-default"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False
    assert [item["name"] for item in finance.list_wallets() if item["is_default"]] == ["Dinheiro"]


def test_agent_simulation_only_queries_server():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    client = ScriptedClient([response_for("simulate_expense", {"amount": "300", "occurred_on": today_local().isoformat(), "wallet_id": wallet["id"]}), final("Se gastar R$ 300, terá R$ 700.")])
    agent = FinancialAgent(finance, model="test", client=client)
    answer = asyncio.run(agent.reply("E se eu gastar R$ 300?", event_key="telegram:2"))
    result = json.loads(client.responses.seen_outputs[0]["output"])
    assert result["data"]["after"] == "700.00"
    assert result["data"]["persisted"] is False
    assert "R$ 700,00" in answer and "Nenhum lançamento foi registrado" in answer
    assert finance.balances()["total"] == "1000.00"


def test_hypothetical_request_cannot_write_even_if_model_calls_write_tool():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=2), key="wallet")
    args = {"kind": "expense", "amount": "35", "occurred_on": (today_local() - timedelta(days=1)).isoformat(), "description": "Padaria", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    client = ScriptedClient([response_for("record_movement", args), final("A simulação não alterou seus dados.")])
    agent = FinancialAgent(finance, model="test", client=client)
    asyncio.run(agent.reply("E se eu tivesse gastado R$ 35 ontem?", event_key="telegram:simulated"))
    result = json.loads(client.responses.seen_outputs[0]["output"])
    assert result["ok"] is False
    assert "Pedido hipotético" in result["error"]
    assert finance.list_movements() == []


def test_read_question_cannot_write_but_immediate_clarification_can():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    args = {"kind": "expense", "amount": "35", "occurred_on": today_local().isoformat(), "description": "Padaria", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}

    client = ScriptedClient([response_for("record_movement", args), final("Nenhum lançamento foi feito.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Quanto gastei na padaria?", event_key="read-only"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False
    assert "record_movement" not in client.responses.offered_tools[0]
    assert finance.list_movements() == []

    history = [{"role": "user", "content": "Gastei R$ 35 na padaria hoje"}, {"role": "assistant", "content": "Qual carteira devo usar?"}]
    client = ScriptedClient([response_for("record_movement", args), final("Registrado.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Banco", history=history, event_key="wallet-followup"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is True
    assert "record_movement" in client.responses.offered_tools[0]
    assert finance.balances()["total"] == "965.00"

    stale_history = [{"role": "user", "content": "Gastei R$ 35 na padaria hoje"}, {"role": "assistant", "content": "Movimento registrado."}]
    client = ScriptedClient([response_for("record_movement", args), final("Nenhum lançamento foi feito.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Banco", history=stale_history, event_key="stale-followup"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False
    assert finance.balances()["total"] == "965.00"

    client = ScriptedClient([response_for("set_default_wallet", {"wallet_id": wallet["id"]}), final("Nenhuma mudança foi feita.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Qual é a carteira padrão?", event_key="default-question"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False
    assert wallet["is_default"] is False


def test_negated_write_requests_and_negative_followup_never_record():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    args = {"kind": "expense", "amount": "35", "occurred_on": today_local().isoformat(), "description": "Padaria", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    for index, message in enumerate(("Não registre o gasto de R$ 35 na padaria", "Por favor, não quero que você registre R$ 35 na padaria", "Só consultar o gasto de R$ 35 na padaria, sem registrar")):
        client = ScriptedClient([response_for("record_movement", args), final("Nenhuma gravação foi feita.")])
        asyncio.run(FinancialAgent(finance, model="test", client=client).reply(message, event_key=f"negative-{index}"))
        assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False

    history = [{"role": "user", "content": "Gastei R$ 35 na padaria"}, {"role": "assistant", "content": "Qual carteira devo usar?"}]
    client = ScriptedClient([response_for("record_movement", args), final("Nenhuma gravação foi feita.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Não, deixe assim", history=history, event_key="negative-followup"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False
    assert finance.balances()["total"] == "1000.00"

    client = ScriptedClient([response_for("confirm_import_batch", {"batch_id": "unknown", "version": 1, "selected_ids": []}), final("Nenhum lote foi confirmado.")])
    asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Posso confirmar o lote?", event_key="permission-question"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False


def test_salary_statement_can_create_schedule_without_changing_current_balance():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    args = {"kind": "income", "amount": "5000", "description": "Salário", "starts_on": (today_local() + timedelta(days=1)).isoformat(), "wallet_id": wallet["id"], "frequency": "monthly", "day_of_month": 5, "estimated": False, "ends_on": None}
    client = ScriptedClient([response_for("create_schedule", args), final("O salário já entrou.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Meu salário de R$ 5.000 entra todo dia 5 na carteira Banco", event_key="salary-schedule"))
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is True
    assert "Programação Salário de R$ 5.000,00" in answer
    assert "já entrou" not in answer
    assert finance.balances()["total"] == "1000.00"


def test_agent_rejects_amount_different_from_explicit_brl_value():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    args = {"kind": "expense", "amount": "350", "occurred_on": today_local().isoformat(), "description": "Padaria", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    client = ScriptedClient([response_for("record_movement", args), final("Preciso confirmar o valor.")])
    agent = FinancialAgent(finance, model="test", client=client)
    asyncio.run(agent.reply("Gastei R$ 35 na padaria hoje", event_key="telegram:wrong-amount"))
    result = json.loads(client.responses.seen_outputs[0]["output"])
    assert result["ok"] is False
    assert "difere do valor" in result["error"]
    assert finance.balances()["total"] == "1000.00"
    assert FinancialAgent._stated_currency_values("R$ 5.000,00 e R$ 35,50") == {5000, Decimal("35.50")}
    assert FinancialAgent._stated_currency_values("-R$ 50,00 ou R$ -12,50") == {Decimal("-50.00"), Decimal("-12.50")}
    assert FinancialAgent._stated_currency_values("35 reais e -12,50 reais") == {Decimal("35"), Decimal("-12.50")}


def test_agent_does_not_claim_unconfirmed_write():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("Registrei o gasto de R$ 35.")]))
    answer = asyncio.run(agent.reply("Gastei R$ 35 hoje", event_key="telegram:3"))
    assert "Não houve confirmação de gravação" in answer
    assert finance.list_movements() == []


def test_agent_blocks_unconfirmed_short_acknowledgements_but_allows_clarification():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("Anotado.")]))
    assert "Não houve confirmação de gravação" in asyncio.run(agent.reply("Gastei R$ 35 na padaria hoje", event_key="unconfirmed-expense"))

    agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("Banco agora é a carteira padrão.")]))
    assert "Não houve confirmação de gravação" in asyncio.run(agent.reply("Defina Banco como carteira padrão", event_key="unconfirmed-default"))

    agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("Banco agora é a carteira padrão.")]))
    assert "Não houve confirmação de gravação" in asyncio.run(agent.reply("Pode definir Banco como carteira padrão?", event_key="unconfirmed-question"))

    agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("Qual carteira devo usar?")]))
    assert asyncio.run(agent.reply("Gastei R$ 35 na padaria hoje", event_key="clarify-wallet")) == "Qual carteira devo usar?"
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("Falta informar a carteira.")]))
    assert asyncio.run(agent.reply("Gastei R$ 35 na padaria hoje", event_key="missing-wallet")) == "Falta informar a carteira."
    assert finance.list_movements() == []


def test_agent_rejects_ungrounded_balance_and_forecast_answers_without_currency_symbol():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    balance_agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("Você tem 999 reais.")]))
    forecast_agent = FinancialAgent(finance, model="test", client=ScriptedClient([final("No fim do mês terá 999 reais.")]))
    assert "Preciso consultar a previsão" in asyncio.run(balance_agent.reply("Quanto tenho?", event_key="telegram:balance"))
    assert "Preciso consultar a previsão" in asyncio.run(forecast_agent.reply("Quanto terei no fim do mês?", event_key="telegram:forecast"))


def test_quanto_tenho_requires_projection_not_just_current_balance():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    client = ScriptedClient([response_for("get_balances", {"wallet_ids": None}), final("Saldo atual: R$ 1.000,00.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Quanto tenho?", event_key="telegram:default-forecast"))
    assert "Preciso consultar a previsão" in answer


def test_quanto_tenho_uses_month_end_even_if_model_requests_another_day():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    finance.create_wallet("Banco", "700", today_local(), key="wallet")
    client = ScriptedClient([response_for("get_projection", {"through": today_local().isoformat(), "wallet_ids": None}), final("Saldo consultado.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Quanto tenho?", event_key="month-end"))
    today = today_local()
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    assert f"Previsão até {month_end.isoformat()}" in answer


def test_balance_and_projection_answers_use_server_amounts():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    due = today_local() + timedelta(days=1)
    finance.create_schedule("income", "200", "Recebimento", due, wallet_id=wallet["id"], key="schedule")

    client = ScriptedClient([response_for("get_balances", {"wallet_ids": None}), final("Seu saldo atual é R$ 999,00.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Qual é meu saldo atual?", event_key="wrong-current"))
    assert "R$ 1.000,00" in answer and "R$ 999,00" not in answer

    client = ScriptedClient([response_for("get_projection", {"through": due.isoformat(), "wallet_ids": None}), final("Saldo atual R$ 999,00; previsto R$ 999,00.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Quanto terei amanhã?", event_key="wrong-projection"))
    assert "R$ 1.000,00" in answer and "R$ 1.200,00" in answer
    assert "R$ 999,00" not in answer


def test_other_read_answers_reject_amounts_absent_from_tool_results():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    finance.record("expense", "35", today_local(), "Padaria", wallet_id=wallet["id"], key="expense")
    args = {"limit": 20, "wallet_id": None}

    client = ScriptedClient([response_for("list_movements", args), final("Seu último gasto foi de R$ 999,00.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Qual foi meu último gasto?", event_key="wrong-read"))
    assert "Não consegui confirmar todos os valores" in answer

    client = ScriptedClient([response_for("list_movements", args), final("Seu último gasto foi de 999 reais.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Qual foi meu último gasto?", event_key="wrong-read-words"))
    assert "Não consegui confirmar todos os valores" in answer

    client = ScriptedClient([response_for("list_movements", args), final("Seu último gasto foi de R$ 35,00.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Qual foi meu último gasto?", event_key="correct-read"))
    assert "R$ 35,00" in answer


def test_amounts_from_repeated_read_tool_calls_are_all_available_to_answer():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    bank = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="bank")
    cash = finance.create_wallet("Dinheiro", "100", today_local() - timedelta(days=1), key="cash")
    finance.record("expense", "35", today_local(), "Padaria", wallet_id=bank["id"], key="bank-expense")
    finance.record("expense", "12", today_local(), "Café", wallet_id=cash["id"], key="cash-expense")
    client = ScriptedClient([
        response_for("list_movements", {"limit": 20, "wallet_id": bank["id"]}),
        response_for("list_movements", {"limit": 20, "wallet_id": cash["id"]}),
        final("Banco: R$ 35,00; Dinheiro: R$ 12,00."),
    ])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Quais os últimos gastos em Banco e Dinheiro?", event_key="two-reads"))
    assert answer == "Banco: R$ 35,00; Dinheiro: R$ 12,00."


def test_investment_question_uses_investment_tool_without_wallet_projection():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    finance.create_investment("Reserva", key="investment")
    client = ScriptedClient([response_for("list_investments", {}), final("Você tem a posição Reserva cadastrada.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Quanto tenho em investimentos?", event_key="telegram:investments"))
    assert "Reserva" in answer


def test_agent_can_explain_failed_projection_without_claiming_a_value():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    finance.create_wallet("Banco", "1000", today_local() - timedelta(days=2), key="wallet")
    client = ScriptedClient([response_for("get_projection", {"through": (today_local() - timedelta(days=1)).isoformat(), "wallet_ids": None}), final("Não consigo projetar para uma data passada. Informe uma data atual ou futura.")])
    answer = asyncio.run(FinancialAgent(finance, model="test", client=client).reply("Qual a previsão de ontem?", event_key="telegram:past"))
    assert "data passada" in answer
    assert json.loads(client.responses.seen_outputs[0]["output"])["ok"] is False


def test_agent_reports_committed_write_if_followup_model_call_fails():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    wallet = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    args = {"kind": "expense", "amount": "35", "occurred_on": today_local().isoformat(), "description": "Padaria", "wallet_id": wallet["id"], "destination_wallet_id": None, "investment_id": None, "occurrence_id": None, "category": "Alimentação", "allow_unlinked": False}
    agent = FinancialAgent(finance, model="test", client=ScriptedClient([response_for("record_movement", args), OSError("API unavailable")]))
    answer = asyncio.run(agent.reply("Gastei R$ 35 na padaria", event_key="telegram:4"))
    assert "Movimento registrado" in answer
    assert finance.balances()["total"] == "965.00"


def test_actual_openai_sdk_serializes_tool_roundtrip_with_mock_transport():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    requests = []

    def response(output, number):
        return {"id": f"resp_{number}", "created_at": int(time.time()), "model": "gpt-5.6", "object": "response", "output": output, "parallel_tool_calls": False, "tool_choice": "auto", "tools": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json=response([{"type": "function_call", "call_id": "call_balance", "name": "get_balances", "arguments": '{"wallet_ids":null}'}], 1))
        return httpx.Response(200, json=response([{"type": "message", "id": "msg_balance", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Saldo atual: R$ 1.000,00.", "annotations": []}]}], 2))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = AsyncOpenAI(api_key="test-key", base_url="https://local.invalid/v1", http_client=http_client)
            return await FinancialAgent(finance, model="gpt-5.6", client=client).reply("Qual meu saldo atual?", event_key="sdk-roundtrip")

    assert "R$ 1.000,00" in asyncio.run(run())
    assert len(requests) == 2
    assert any(item.get("type") == "function_call" for item in requests[1]["input"] if isinstance(item, dict))
    assert any(item.get("type") == "function_call_output" and '"total": "1000.00"' in item["output"] for item in requests[1]["input"] if isinstance(item, dict))


def test_actual_openai_sdk_parses_document_extraction_with_mock_transport():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "owner")
    finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="wallet")
    seen = []
    extracted = {"items": [{"occurred_on": today_local().isoformat(), "description": "Padaria", "amount": "35", "direction": "expense", "wallet_name": "Banco", "category": "Alimentação", "uncertain": False}]}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"id": "resp_document", "created_at": int(time.time()), "model": "gpt-5.6", "object": "response", "output": [{"type": "message", "id": "msg_document", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": json.dumps(extracted, ensure_ascii=False), "annotations": []}]}], "parallel_tool_calls": False, "tool_choice": "auto", "tools": []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = AsyncOpenAI(api_key="test-key", base_url="https://local.invalid/v1", http_client=http_client)
            return await FinancialAgent(finance, model="gpt-5.6", client=client).import_document(example_statement_pdf(today_local()), "extrato-teste.pdf", "application/pdf")

    batch = asyncio.run(run())
    assert len(batch["items"]) == 1
    assert batch["items"][0]["amount"] == "35.00"
    assert finance.list_movements() == []
    assert seen[0]["input"][0]["content"][1]["type"] == "input_file"


def test_shared_evaluation_checks_tools_and_read_only_history():
    class Agent:
        used_tools = []

        async def reply(self, message, *, event_key, history):
            self.used_tools.append("get_balances" if event_key.endswith(":1") else "record_movement")
            return "R$ 100"

    checks = []
    history = []
    agent = Agent()
    asyncio.run(evaluate(agent, checks, "saldo", "Quanto tenho?", lambda answer: "100" in answer, prefix="test", required_tools={"get_balances"}, read_only=True, history=history))
    asyncio.run(evaluate(agent, checks, "consulta", "Mostre o saldo", lambda answer: True, prefix="test", required_tools={"get_balances"}, read_only=True, history=history))

    assert [row[1] for row in checks] == [True, False]
    assert [item["role"] for item in history] == ["user", "assistant", "user", "assistant"]
