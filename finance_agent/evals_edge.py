import asyncio
from datetime import timedelta
from functools import partial
from openai import AsyncOpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from finance_agent.config import load_ai_settings
from finance_agent.database import Base
from finance_agent.evals import TracedAgent, evaluate
from finance_agent.imports import ExtractedItem, Extraction, ImportService
from finance_agent.service import FinanceService, today_local


async def main():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "edge-test")
    today = today_local()
    bank = finance.create_wallet("Banco", "1000", today, key="bank")
    investment = finance.create_investment("Reserva", key="investment")
    finance.record("contribution", "100", today, "Aporte", wallet_id=bank["id"], investment_id=investment["id"], key="contribution")
    tomorrow = today + timedelta(days=1)
    monthly = finance.create_schedule("expense", "40", "Academia", tomorrow, wallet_id=bank["id"], frequency="monthly", key="monthly")
    once = finance.create_schedule("expense", "20", "Conta única", tomorrow, wallet_id=bank["id"], key="once")
    occurrence = next(o for o in finance.list_occurrences(through=tomorrow) if o["schedule_id"] == once["id"])
    imports = ImportService(finance)
    extracted = Extraction(items=[ExtractedItem(occurred_on=today.isoformat(), description="Táxi", amount="18", direction="expense", wallet_name="Banco", category="Outros", uncertain=False)])
    batch = imports.create_batch(b"synthetic-taxi-statement", "taxi.pdf", "application/pdf", extracted)
    settings = load_ai_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    agent = TracedAgent(finance, model=settings.openai_model, client=client)
    checks = []
    exercise = partial(evaluate, agent, checks, prefix="edge", stream=True)

    try:
        await exercise("resgate", "Resgatei R$ 50 da Reserva para a carteira Banco hoje.", lambda _: any(m["kind"] == "withdrawal" and m["amount"] == "50.00" for m in finance.list_movements()), "record_movement")
        await exercise("editar programação", f"Altere a programação Academia ID {monthly['id']} a partir de {tomorrow.isoformat()}: R$ 45 mensais, todo dia {tomorrow.day}, na carteira Banco.", lambda _: any(s["id"] == monthly["id"] and not s["active"] for s in finance.list_schedules()) and any(s["description"] == "Academia" and s["amount"] == "45.00" and s["active"] for s in finance.list_schedules()), "edit_schedule")
        await exercise("cancelar ocorrência", f"Cancele apenas a ocorrência {occurrence['id']} da Conta única.", lambda _: any(o["id"] == occurrence["id"] and o["status"] == "cancelled" for o in finance.list_occurrences(through=tomorrow)), "cancel_occurrence")
        await exercise("editar proposta importada", f"Corrija a categoria do item 1 do lote {batch['id']} para Transporte.", lambda _: imports.get_batch(batch["id"])["items"][0]["category"] == "Transporte", "edit_import_item")
        await exercise("confirmar item importado", f"Confirme o item 1 do lote {batch['id']}.", lambda _: imports.get_batch(batch["id"])["status"] == "confirmed" and any(m["description"] == "Táxi" and m["amount"] == "18.00" for m in finance.list_movements()), "confirm_import_batch")
    finally:
        await client.close()
        engine.dispose()
    print(f"TOTAL: {sum(passed for _, passed, _, _ in checks)}/{len(checks)}", flush=True)
    return 0 if all(passed for _, passed, _, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
