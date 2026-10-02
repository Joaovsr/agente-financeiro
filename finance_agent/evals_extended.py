"""Casos adicionais de conversa com dados fictícios e banco temporário."""

import asyncio
import sys
from datetime import timedelta
from functools import partial

from openai import AsyncOpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from finance_agent.config import load_ai_settings
from finance_agent.database import Base
from finance_agent.evals import TracedAgent, evaluate, mentions_amount
from finance_agent.notifications import NotificationService
from finance_agent.service import FinanceService, today_local


async def main():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "extra-evaluation-owner")
    today = today_local()
    bank = finance.create_wallet("Banco", "1000", today, key="setup-bank")
    cash = finance.create_wallet("Dinheiro", "200", today, key="setup-cash")
    finance.set_default_wallet(bank["id"], key="setup-default")
    config = load_ai_settings()
    client = AsyncOpenAI(api_key=config.openai_api_key)
    agent = TracedAgent(finance, model=config.openai_model, client=client)
    checks = []
    exercise = partial(evaluate, agent, checks, prefix="extra", stream=True)

    try:
        await exercise("entrada", "Recebi R$ 120 de reembolso hoje na carteira Banco.", lambda _: any(m["kind"] == "income" and m["amount"] == "120.00" for m in finance.list_movements()), "record_movement")
        await exercise("transferência", "Transfira R$ 50 da carteira Banco para a carteira Dinheiro hoje.", lambda _: any(m["kind"] == "transfer" and m["amount"] == "50.00" and m["wallet_id"] == bank["id"] and m["destination_wallet_id"] == cash["id"] for m in finance.list_movements()), "record_movement")
        await exercise("criar investimento", "Crie o investimento Tesouro.", lambda _: any(i["name"] == "Tesouro" for i in finance.list_investments()), "create_investment")
        await exercise("avaliar investimento", "Informe que o valor atual do investimento Tesouro é R$ 150 hoje.", lambda _: any(i["name"] == "Tesouro" and i["current_value"] == "150.00" for i in finance.list_investments()), "value_investment")

        expense = finance.record("expense", "12", today, "Café", wallet_id=bank["id"], category="Alimentação", key="setup-expense")
        await exercise("corrigir gasto", f"Corrija o movimento {expense['id']}: o gasto de Café foi R$ 10, não R$ 12.", lambda _: any(m["kind"] == "expense" and m["amount"] == "10.00" and m["description"] == "Café" for m in finance.list_movements()), "correct_movement")
        corrected = next((m for m in finance.list_movements() if m["description"] == "Café" and m["amount"] == "10.00"), None)
        if corrected:
            await exercise("estornar gasto", f"Estorne o movimento de gasto {corrected['id']}.", lambda _: not any(m["id"] == corrected["id"] for m in finance.list_movements()), "void_movement")

        await exercise("reconciliar saldo", "Ajuste o saldo real da carteira Banco para R$ 900 hoje.", lambda _: any(w["wallet_id"] == bank["id"] and w["balance"] == "900.00" for w in finance.balances()["wallets"]), "reconcile_wallet")
        finance.record("expense", "30", today, "Almoço", wallet_id=bank["id"], category="Alimentação", key="setup-lunch")
        aggregate = finance.aggregates(today.replace(day=1), today)
        await exercise("resumo por categoria", "Quanto gastei e recebi neste mês por categoria?", lambda answer: mentions_amount(answer, aggregate["expense"]) and mentions_amount(answer, aggregate["income"]), "get_aggregates")

        schedule = finance.create_schedule("expense", "60", "Internet", today, wallet_id=bank["id"], key="setup-internet")
        occurrence = next(o for o in finance.list_occurrences(through=today) if o["schedule_id"] == schedule["id"])
        await exercise("confirmar programação", f"Confirme a ocorrência {occurrence['id']} de Internet: paguei R$ 60 hoje.", lambda _: any(m["occurrence_id"] == occurrence["id"] and m["amount"] == "60.00" for m in finance.list_movements()), "confirm_occurrence")

        future = finance.create_schedule("expense", "40", "Academia", today + timedelta(days=1), wallet_id=bank["id"], key="setup-academia")
        await exercise("cancelar programação", f"Cancele a programação de Academia com ID {future['id']}.", lambda _: any(s["id"] == future["id"] and not s["active"] for s in finance.list_schedules()), "cancel_schedule")
        await exercise("configurar avisos", "Ative lembretes 2 dias antes, alerta de saldo negativo e resumo mensal.", lambda _: NotificationService(finance).get_settings() == {"enabled": True, "reminder_days_before": 2, "negative_alert": True, "monthly_summary": True}, "configure_notifications")
        await exercise("consultar investimentos", "Quais investimentos tenho e quanto valem?", lambda answer: "Tesouro" in answer and mentions_amount(answer, "150"), "list_investments")
    finally:
        await client.close()
        engine.dispose()

    print(f"TOTAL: {sum(passed for _, passed, _, _ in checks)}/{len(checks)}", flush=True)
    return 0 if all(passed for _, passed, _, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
