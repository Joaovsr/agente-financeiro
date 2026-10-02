"""Avaliação com API real e dados fictícios, sem Telegram ou banco de produção."""

import asyncio
import re
import sys
from calendar import monthrange
from datetime import date
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import partial

from openai import AsyncOpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .agent import FinancialAgent, WRITE_TOOLS
from .config import load_ai_settings
from .database import Base
from .service import FinanceService, today_local


class TracedAgent(FinancialAgent):
    """Registra apenas ferramentas executadas com sucesso durante a avaliação."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.used_tools: list[str] = []

    def _call(self, name: str, args: dict, key: str | None) -> object:
        result = super()._call(name, args, key)
        self.used_tools.append(name)
        return result


async def evaluate(agent, checks, label, message, predicate, required_tool=None, *, prefix, required_tools=None, read_only=False, history=None, stream=False):
    start = len(agent.used_tools)
    try:
        answer = await agent.reply(message, event_key=f"{prefix}:{len(checks) + 1}", history=history)
        used = set(agent.used_tools[start:])
        required = required_tools or ({required_tool} if required_tool else set())
        passed = bool(predicate(answer)) and required <= used and (not read_only or not (used & WRITE_TOOLS))
        if history is not None:
            history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": answer}])
        result = (label, passed, answer, sorted(used))
    except Exception as exc:
        result = (label, False, f"{type(exc).__name__}: {exc}", agent.used_tools[start:])
    checks.append(result)
    if stream:
        print(f"{'PASSOU' if result[1] else 'FALHOU'} | {label} | {', '.join(result[3]) or 'nenhuma'} | {result[2][:250]}", flush=True)


def mentions_amount(answer: str, expected: str) -> bool:
    target = Decimal(expected)
    for token in re.findall(r"(?<!\d)-?\d[\d.,]*", answer):
        token = token.rstrip(".,")
        if not token:
            continue
        if "," in token:
            token = token.replace(".", "").replace(",", ".")
        elif token.count(".") == 1 and len(token.rsplit(".", 1)[1]) == 3:
            token = token.replace(".", "")
        try:
            if Decimal(token) == target:
                return True
        except InvalidOperation:
            continue
    return False


def example_statement_pdf(day: date) -> bytes:
    """Gera um extrato fictício mínimo para avaliar a extração de PDF."""
    lines = [
        "Extrato ficticio para avaliacao",
        f"{day.isoformat()} DESPESA Padaria BRL 35.00",
        f"{day.isoformat()} DESPESA Padaria BRL 35.00",
    ]
    content = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({line}) Tj 0 -20 Td" for line in lines) + " ET"
    stream = content.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(pdf)


async def run() -> int:
    config = load_ai_settings()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    finance = FinanceService(sessionmaker(bind=engine, expire_on_commit=False), "evaluation-owner")
    bank = finance.create_wallet("Banco", "1000", today_local() - timedelta(days=1), key="evaluation-wallet")
    investment = finance.create_investment("Reserva", key="evaluation-investment")
    client = AsyncOpenAI(api_key=config.openai_api_key)
    agent = TracedAgent(finance, model=config.openai_model, client=client)
    checks = []
    history: list[dict] = []
    exercise = partial(evaluate, agent, checks, prefix="evaluation", history=history)

    try:
        await exercise("despesa realizada", "Gastei R$ 35 na padaria hoje pela carteira Banco.", lambda _: any(row["kind"] == "expense" and row["amount"] == "35.00" for row in finance.list_movements()))
        await exercise("programação mensal", "Meu salário de R$ 5.000 entra todo dia 5 na carteira Banco. Cadastre a programação mensal.", lambda _: any(row["kind"] == "income" and row["frequency"] == "monthly" and row["amount"] == "5000.00" for row in finance.list_schedules()))
        before_document = len(finance.list_movements())
        try:
            batch = await agent.import_document(example_statement_pdf(today_local()), "extrato-avaliacao.pdf", "application/pdf")
            flagged = any(item["duplicate_movement_ids"] or item["repeated_in_file"] for item in batch["items"])
            checks.append(("PDF com lançamento repetido", len(batch["items"]) >= 2 and flagged and len(finance.list_movements()) == before_document, agent.describe_batch(batch), []))
        except Exception as exc:
            checks.append(("PDF com lançamento repetido", False, f"{type(exc).__name__}: {exc}", []))
        before = len(finance.list_movements())
        current_day = today_local()
        month_end = date(current_day.year, current_day.month, monthrange(current_day.year, current_day.month)[1])
        await exercise("consulta de saldo", "Quanto tenho? Mostre o saldo atual e a previsão até o fim do mês.", lambda answer: len(finance.list_movements()) == before and mentions_amount(answer, finance.balances()["total"]) and mentions_amount(answer, finance.projection(month_end)["projected_total"]), required_tools={"get_projection"}, read_only=True)
        await exercise("previsão", "Quanto terei no fim do mês?", lambda answer: len(finance.list_movements()) == before and mentions_amount(answer, finance.projection(month_end)["projected_total"]), required_tools={"get_projection"}, read_only=True)
        await exercise("aporte", "Aportei R$ 200 na Reserva hoje, saindo da carteira Banco.", lambda _: any(row["kind"] == "contribution" and row["investment_id"] == investment["id"] for row in finance.list_movements()))
        before = len(finance.list_movements())
        tomorrow = today_local() + timedelta(days=1)
        simulated = finance.simulate_expense("300", tomorrow)
        await exercise("hipótese sem gravação", "E se eu gastar R$ 300 amanhã pela carteira Banco?", lambda answer: len(finance.list_movements()) == before and mentions_amount(answer, simulated["after"]), required_tools={"simulate_expense"}, read_only=True)

        finance.create_wallet("Dinheiro", "200", today_local() - timedelta(days=1), key="evaluation-cash")
        before = len(finance.list_movements())
        await exercise(
            "saldo por carteira e total",
            "Quanto tenho em cada carteira e no total? Mostre também a previsão até o fim do mês.",
            lambda answer: (
                len(finance.list_movements()) == before
                and all(wallet["name"] in answer and mentions_amount(answer, wallet["balance"]) for wallet in finance.balances()["wallets"])
                and mentions_amount(answer, finance.balances()["total"])
                and mentions_amount(answer, finance.projection(month_end)["projected_total"])
            ),
            required_tools={"get_projection"},
            read_only=True,
        )
        await exercise("carteira ambígua", "Gastei R$ 12 em café hoje.", lambda answer: len(finance.list_movements()) == before and "carteira" in answer.casefold(), read_only=True)
        await exercise("escolha da carteira padrão", "Defina Banco como carteira padrão.", lambda _: any(row["name"] == "Banco" and row["is_default"] for row in finance.list_wallets()), required_tools={"set_default_wallet"})
        await exercise("gasto na carteira padrão", "Gastei R$ 12 em café hoje.", lambda _: any(row["kind"] == "expense" and row["amount"] == "12.00" and row["wallet_id"] == bank["id"] for row in finance.list_movements()), required_tools={"record_movement"})
    finally:
        await client.close()
        engine.dispose()

    for label, passed, answer, used in checks:
        print(f"{'PASSOU' if passed else 'FALHOU'} | {label} | ferramentas: {', '.join(used) or 'nenhuma'} | {answer[:300]}")
    return 0 if all(passed for _, passed, _, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
