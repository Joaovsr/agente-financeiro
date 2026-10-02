"""Regras financeiras. Nenhuma regra de saldo depende do modelo de linguagem."""

import calendar
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .models import Command, Investment, Movement, Occurrence, Schedule, Wallet

LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")
CENT = Decimal("0.01")


class DomainError(ValueError):
    pass


def today_local() -> date:
    return datetime.now(LOCAL_ZONE).date()


def money(value: str | Decimal | int, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DomainError("Valor monetário inválido") from exc
    if not result.is_finite() or result.as_tuple().exponent < -2:
        raise DomainError("Use valores em reais com até dois centavos")
    if positive and result <= 0:
        raise DomainError("O valor deve ser maior que zero")
    if abs(result) > Decimal("9999999999999999.99"):
        raise DomainError("Valor acima do limite permitido")
    return result.quantize(CENT)


def brl(value: Decimal) -> str:
    prefix = "-" if value < 0 else ""
    formatted = f"{abs(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{prefix}R$ {formatted}"


def similar_description(left: str, right: str) -> bool:
    def words(value: str) -> set[str]:
        plain = "".join(char for char in unicodedata.normalize("NFD", value.casefold()) if not unicodedata.combining(char))
        return {word for word in re.findall(r"[a-z0-9]+", plain) if len(word) >= 4}

    return bool(words(left) & words(right))


def bounded_text(value: str, limit: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"Informe {label.lower()}")
    result = value.strip()
    if len(result) > limit:
        raise DomainError(f"{label} deve ter no máximo {limit} caracteres")
    return result


def optional_category(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 100:
        raise DomainError("Categoria deve ter no máximo 100 caracteres")
    return value


class FinanceService:
    def __init__(self, factory: sessionmaker[Session], owner_id: str):
        if not owner_id:
            raise ValueError("owner_id obrigatório")
        self.factory = factory
        self.owner_id = owner_id

    def _wallet(self, session: Session, wallet_id: str, *, active: bool = True) -> Wallet:
        wallet = session.scalar(select(Wallet).where(Wallet.id == wallet_id, Wallet.owner_id == self.owner_id))
        if wallet is None or (active and not wallet.active):
            raise DomainError("Carteira não encontrada ou inativa")
        return wallet

    def _investment(self, session: Session, investment_id: str) -> Investment:
        investment = session.scalar(select(Investment).where(Investment.id == investment_id, Investment.owner_id == self.owner_id))
        if investment is None:
            raise DomainError("Investimento não encontrado")
        return investment

    def _fingerprint(self, payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()

    def _command(self, session: Session, key: str, payload: dict) -> dict | None:
        if not key or len(key) > 180:
            raise DomainError("Chave de operação inválida")
        command = session.scalar(select(Command).where(Command.owner_id == self.owner_id, Command.key == key))
        if command and command.request_hash != self._fingerprint(payload):
            raise DomainError("A mesma mensagem tentou executar comandos diferentes; revise antes de reenviar")
        return command.result if command else None

    def _save_command(self, session: Session, key: str, payload: dict, result: dict) -> dict:
        session.add(Command(owner_id=self.owner_id, key=key, action=payload.get("action"), request_hash=self._fingerprint(payload), result=result))
        return result

    def create_wallet(self, name: str, opening_balance: str, opened_on: date, *, key: str, is_default: bool = False) -> dict:
        name = bounded_text(name, 100, "Nome da carteira")
        if opened_on > today_local():
            raise DomainError("Data do saldo inicial não pode ser futura")
        payload = {"action": "create_wallet", "name": name, "opening_balance": str(money(opening_balance, positive=False)), "opened_on": opened_on, "is_default": is_default}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            if session.scalar(select(Wallet.id).where(Wallet.owner_id == self.owner_id, Wallet.name == name)):
                raise DomainError("Já existe carteira com esse nome")
            balance = money(opening_balance, positive=False)
            has_default = session.scalar(select(Wallet.id).where(Wallet.owner_id == self.owner_id, Wallet.is_default.is_(True)))
            if is_default and has_default:
                raise DomainError("Já existe uma carteira padrão")
            wallet = Wallet(owner_id=self.owner_id, name=name, opening_balance=balance, opened_on=opened_on, is_default=is_default)
            session.add(wallet)
            session.flush()
            return self._save_command(session, key, payload, {"id": wallet.id, "name": wallet.name, "opening_balance": str(balance), "opened_on": opened_on.isoformat(), "is_default": wallet.is_default})

    def list_wallets(self) -> list[dict]:
        with self.factory() as session:
            wallets = session.scalars(select(Wallet).where(Wallet.owner_id == self.owner_id, Wallet.active.is_(True)).order_by(Wallet.name)).all()
            return [{"id": w.id, "name": w.name, "is_default": w.is_default, "opened_on": w.opened_on.isoformat()} for w in wallets]

    def set_default_wallet(self, wallet_id: str | None, *, key: str) -> dict:
        payload = {"action": "set_default_wallet", "wallet_id": wallet_id}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            selected = self._wallet(session, wallet_id) if wallet_id is not None else None
            wallets = session.scalars(select(Wallet).where(Wallet.owner_id == self.owner_id)).all()
            for wallet in wallets:
                wallet.is_default = selected is not None and wallet.id == selected.id
            return self._save_command(session, key, payload, {"wallet_id": wallet_id, "name": selected.name if selected else None})

    def resolve_wallet(self, session: Session, wallet_id: str | None) -> Wallet:
        if wallet_id:
            return self._wallet(session, wallet_id)
        wallets = session.scalars(select(Wallet).where(Wallet.owner_id == self.owner_id, Wallet.active.is_(True))).all()
        if len(wallets) == 1:
            return wallets[0]
        defaults = [w for w in wallets if w.is_default]
        if len(defaults) == 1:
            return defaults[0]
        raise DomainError("Informe a carteira")

    def _movement_dict(self, movement: Movement) -> dict:
        return {"id": movement.id, "kind": movement.kind, "wallet_id": movement.wallet_id, "destination_wallet_id": movement.destination_wallet_id, "investment_id": movement.investment_id, "occurrence_id": movement.occurrence_id, "amount": str(movement.amount), "occurred_on": movement.occurred_on.isoformat(), "description": movement.description, "category": movement.category}

    def _wallet_balance(self, session: Session, wallet: Wallet, as_of: date) -> Decimal:
        rows = session.scalars(select(Movement).where(Movement.owner_id == self.owner_id, Movement.voided_at.is_(None), Movement.occurred_on <= as_of, or_(Movement.wallet_id == wallet.id, Movement.destination_wallet_id == wallet.id))).all()
        balance = wallet.opening_balance
        for row in rows:
            if row.wallet_id == wallet.id:
                if row.kind in {"income", "withdrawal", "adjustment"}:
                    balance += row.amount
                elif row.kind in {"expense", "transfer", "contribution"}:
                    balance -= row.amount
            if row.kind == "transfer" and row.destination_wallet_id == wallet.id:
                balance += row.amount
        return balance.quantize(CENT)

    def reconcile_wallet(self, wallet_id: str, target_balance: str, occurred_on: date, *, key: str) -> dict:
        target = money(target_balance, positive=False)
        if occurred_on > today_local():
            raise DomainError("Reconciliação não pode ter data futura")
        payload = {"action": "reconcile_wallet", "wallet_id": wallet_id, "target_balance": str(target), "occurred_on": occurred_on}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            wallet = self._wallet(session, wallet_id)
            if occurred_on < wallet.opened_on:
                raise DomainError("Data anterior à abertura da carteira")
            before = self._wallet_balance(session, wallet, occurred_on)
            delta = target - before
            if delta == 0:
                return self._save_command(session, key, payload, {"wallet_id": wallet.id, "before": str(before), "after": str(target), "adjustment_id": None})
            movement = Movement(owner_id=self.owner_id, kind="adjustment", wallet_id=wallet.id, amount=delta, occurred_on=occurred_on, description="Reconciliação de saldo", source="reconciliation")
            session.add(movement)
            session.flush()
            return self._save_command(session, key, payload, {"wallet_id": wallet.id, "before": str(before), "after": str(target), "difference": str(delta), "adjustment_id": movement.id})

    def record(self, kind: str, amount: str, occurred_on: date, description: str, *, key: str, wallet_id: str | None = None, destination_wallet_id: str | None = None, investment_id: str | None = None, occurrence_id: str | None = None, category: str | None = None, source: str = "conversation", allow_unlinked: bool = False) -> dict:
        if kind not in {"income", "expense", "transfer", "contribution", "withdrawal"}:
            raise DomainError("Tipo de movimento inválido")
        value = money(amount)
        description = bounded_text(description, 300, "Descrição")
        category = optional_category(category)
        if occurred_on > today_local():
            raise DomainError("Movimento realizado não pode ter data futura; crie uma programação")
        payload = {"action": "record", "kind": kind, "amount": str(value), "occurred_on": occurred_on, "description": description, "wallet_id": wallet_id, "destination_wallet_id": destination_wallet_id, "investment_id": investment_id, "occurrence_id": occurrence_id, "category": category, "source": source, "allow_unlinked": allow_unlinked}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            wallet = self.resolve_wallet(session, wallet_id)
            if occurred_on < wallet.opened_on:
                raise DomainError("Data anterior à abertura da carteira")
            if kind == "transfer":
                destination = self._wallet(session, destination_wallet_id or "")
                if wallet.id == destination.id:
                    raise DomainError("Origem e destino devem ser diferentes")
                if occurred_on < destination.opened_on:
                    raise DomainError("Data anterior à abertura da carteira de destino")
            elif destination_wallet_id:
                raise DomainError("Destino só é permitido em transferências")
            if kind in {"contribution", "withdrawal"}:
                self._investment(session, investment_id or "")
            elif investment_id:
                raise DomainError("Investimento inválido para este movimento")
            if kind in {"income", "expense"} and not occurrence_id and not allow_unlinked:
                self._ensure_occurrences(session, occurred_on + timedelta(days=3))
                possible = session.execute(select(Occurrence, Schedule).join(Schedule, Occurrence.schedule_id == Schedule.id).where(Schedule.owner_id == self.owner_id, Schedule.wallet_id == wallet.id, Schedule.kind == kind, Occurrence.status == "pending", Occurrence.due_on.between(occurred_on - timedelta(days=3), occurred_on + timedelta(days=3)))).all()
                matches = [(o, s) for o, s in possible if o.due_on == occurred_on or s.amount == value or similar_description(description, s.description)]
                if matches:
                    ids = ", ".join(o.id for o, _ in matches[:3])
                    raise DomainError(f"Possível programação correspondente: {ids}. Confirme o vínculo ou peça lançamento separado")
            if occurrence_id:
                if kind not in {"income", "expense"}:
                    raise DomainError("Somente entradas e despesas confirmam programações")
                occurrence = session.get(Occurrence, occurrence_id)
                schedule = session.get(Schedule, occurrence.schedule_id) if occurrence else None
                if schedule is None or schedule.owner_id != self.owner_id or occurrence.status != "pending":
                    raise DomainError("Ocorrência indisponível")
                if schedule.wallet_id != wallet.id or schedule.kind != kind:
                    raise DomainError("Movimento incompatível com a programação")
                occurrence.status = "confirmed"
                occurrence.actual_amount = value
            movement = Movement(owner_id=self.owner_id, kind=kind, wallet_id=wallet.id, destination_wallet_id=destination_wallet_id, investment_id=investment_id, occurrence_id=occurrence_id, amount=value, occurred_on=occurred_on, description=description, category=category or ("Outros" if kind == "expense" else None), source=source)
            session.add(movement)
            session.flush()
            return self._save_command(session, key, payload, self._movement_dict(movement))

    def list_movements(self, *, limit: int = 30, wallet_id: str | None = None) -> list[dict]:
        if not 1 <= limit <= 100:
            raise DomainError("Limite deve estar entre 1 e 100")
        with self.factory() as session:
            query = select(Movement).where(Movement.owner_id == self.owner_id, Movement.voided_at.is_(None))
            if wallet_id:
                self._wallet(session, wallet_id, active=False)
                query = query.where(or_(Movement.wallet_id == wallet_id, Movement.destination_wallet_id == wallet_id))
            rows = session.scalars(query.order_by(Movement.occurred_on.desc(), Movement.created_at.desc()).limit(limit)).all()
            return [self._movement_dict(m) for m in rows]

    def movement_history(self, movement_id: str) -> list[dict]:
        with self.factory() as session:
            row = session.scalar(select(Movement).where(Movement.id == movement_id, Movement.owner_id == self.owner_id))
            if row is None:
                raise DomainError("Movimento não encontrado")
            while row.replaces_id:
                row = session.scalar(select(Movement).where(Movement.id == row.replaces_id, Movement.owner_id == self.owner_id))
                if row is None:
                    raise DomainError("Histórico de correção inconsistente")
            history = []
            while row:
                item = self._movement_dict(row)
                item.update({"voided_at": row.voided_at.isoformat() if row.voided_at else None, "replaces_id": row.replaces_id, "prior_occurrence_id": row.prior_occurrence_id})
                history.append(item)
                row = session.scalar(select(Movement).where(Movement.replaces_id == row.id, Movement.owner_id == self.owner_id))
            return history

    def void_movement(self, movement_id: str, *, key: str) -> dict:
        payload = {"action": "void_movement", "movement_id": movement_id}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            movement = session.scalar(select(Movement).where(Movement.id == movement_id, Movement.owner_id == self.owner_id, Movement.voided_at.is_(None)))
            if movement is None:
                raise DomainError("Movimento não encontrado ou já estornado")
            original = self._movement_dict(movement)
            movement.voided_at = datetime.now(timezone.utc)
            if movement.occurrence_id:
                occurrence = session.get(Occurrence, movement.occurrence_id)
                occurrence.status = "pending"
                occurrence.actual_amount = None
                movement.prior_occurrence_id = movement.occurrence_id
                movement.occurrence_id = None
            return self._save_command(session, key, payload, {"voided": original})

    def correct_movement(self, movement_id: str, *, key: str, amount: str | None = None, occurred_on: date | None = None, description: str | None = None, wallet_id: str | None = None, category: str | None = None) -> dict:
        if description is not None:
            description = bounded_text(description, 300, "Descrição")
        category = optional_category(category)
        payload = {"action": "correct_movement", "movement_id": movement_id, "amount": amount, "occurred_on": occurred_on, "description": description, "wallet_id": wallet_id, "category": category}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            old = session.scalar(select(Movement).where(Movement.id == movement_id, Movement.owner_id == self.owner_id, Movement.voided_at.is_(None)))
            if old is None:
                raise DomainError("Movimento não encontrado ou já corrigido")
            new_wallet = self._wallet(session, wallet_id or old.wallet_id)
            new_date = occurred_on or old.occurred_on
            if new_date > today_local() or new_date < new_wallet.opened_on:
                raise DomainError("Data inválida para a carteira")
            if old.occurrence_id and new_wallet.id != old.wallet_id:
                raise DomainError("Desvincule a programação antes de mudar a carteira")
            if old.kind == "transfer":
                destination = self._wallet(session, old.destination_wallet_id or "")
                if new_wallet.id == destination.id or new_date < destination.opened_on:
                    raise DomainError("Transferência incompatível com a nova carteira ou data")
            new_description = description if description is not None else old.description
            new_amount = money(amount, positive=old.kind != "adjustment") if amount is not None else old.amount
            old_snapshot = self._movement_dict(old)
            occurrence_id = old.occurrence_id
            old.voided_at = datetime.now(timezone.utc)
            old.prior_occurrence_id = occurrence_id
            old.occurrence_id = None
            if occurrence_id:
                session.get(Occurrence, occurrence_id).actual_amount = new_amount
            replacement = Movement(owner_id=self.owner_id, kind=old.kind, wallet_id=new_wallet.id, destination_wallet_id=old.destination_wallet_id, investment_id=old.investment_id, occurrence_id=occurrence_id, amount=new_amount, occurred_on=new_date, description=new_description, category=category if category is not None else old.category, source="correction", replaces_id=old.id)
            session.add(replacement)
            session.flush()
            return self._save_command(session, key, payload, {"previous": old_snapshot, "current": self._movement_dict(replacement)})

    def balances(self, *, as_of: date | None = None, wallet_ids: list[str] | None = None) -> dict:
        if wallet_ids is not None and not wallet_ids:
            raise DomainError("Selecione ao menos uma carteira")
        with self.factory() as session:
            query = select(Wallet).where(Wallet.owner_id == self.owner_id, Wallet.active.is_(True))
            if wallet_ids is not None:
                query = query.where(Wallet.id.in_(wallet_ids))
            wallets = session.scalars(query.order_by(Wallet.name)).all()
            if wallet_ids is not None and len(set(wallet_ids)) != len(wallets):
                raise DomainError("Carteira inválida na seleção")
            result = []
            for wallet in wallets:
                if (as_of or today_local()) < wallet.opened_on:
                    raise DomainError("Data anterior à abertura de uma carteira")
                balance = self._wallet_balance(session, wallet, as_of or today_local())
                result.append({"wallet_id": wallet.id, "name": wallet.name, "balance": str(balance.quantize(CENT))})
            return {"as_of": (as_of or today_local()).isoformat(), "wallets": result, "total": str(sum((Decimal(item["balance"]) for item in result), Decimal("0.00")))}

    def create_investment(self, name: str, *, key: str) -> dict:
        name = bounded_text(name, 100, "Nome do investimento")
        payload = {"action": "create_investment", "name": name}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            if session.scalar(select(Investment.id).where(Investment.owner_id == self.owner_id, Investment.name == name)):
                raise DomainError("Investimento já existe")
            investment = Investment(owner_id=self.owner_id, name=name)
            session.add(investment)
            session.flush()
            return self._save_command(session, key, payload, {"id": investment.id, "name": investment.name})

    def value_investment(self, investment_id: str, value: str, valued_on: date, *, key: str) -> dict:
        current_value = money(value, positive=False)
        if current_value < 0:
            raise DomainError("Valor atual do investimento não pode ser negativo")
        if valued_on > today_local():
            raise DomainError("Avaliação realizada não pode ter data futura")
        payload = {"action": "value_investment", "investment_id": investment_id, "value": str(current_value), "valued_on": valued_on}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            investment = self._investment(session, investment_id)
            investment.current_value = current_value
            investment.valued_on = valued_on
            return self._save_command(session, key, payload, {"id": investment.id, "current_value": str(investment.current_value), "valued_on": valued_on.isoformat()})

    def list_investments(self) -> list[dict]:
        with self.factory() as session:
            investments = session.scalars(select(Investment).where(Investment.owner_id == self.owner_id).order_by(Investment.name)).all()
            movements = session.scalars(select(Movement).where(Movement.owner_id == self.owner_id, Movement.kind.in_(["contribution", "withdrawal"]), Movement.voided_at.is_(None))).all()
            result = []
            for investment in investments:
                contribution = sum((row.amount for row in movements if row.investment_id == investment.id and row.kind == "contribution"), Decimal("0.00"))
                withdrawal = sum((row.amount for row in movements if row.investment_id == investment.id and row.kind == "withdrawal"), Decimal("0.00"))
                result.append({"id": investment.id, "name": investment.name, "contributed": str(contribution), "withdrawn": str(withdrawal), "net_contributed": str(contribution - withdrawal), "current_value": str(investment.current_value) if investment.current_value is not None else None, "valued_on": investment.valued_on.isoformat() if investment.valued_on else None})
            return result

    def create_schedule(self, kind: str, amount: str, description: str, starts_on: date, *, key: str, wallet_id: str | None = None, frequency: str = "once", day_of_month: int | None = None, estimated: bool = False, ends_on: date | None = None) -> dict:
        if kind not in {"income", "expense"} or frequency not in {"once", "monthly"}:
            raise DomainError("Programação inválida")
        description = bounded_text(description, 300, "Descrição")
        if frequency == "monthly" and not 1 <= (starts_on.day if day_of_month is None else day_of_month) <= 31:
            raise DomainError("Dia do mês inválido")
        if ends_on and ends_on < starts_on:
            raise DomainError("Fim anterior ao início")
        value = money(amount)
        payload = {"action": "create_schedule", "kind": kind, "amount": str(value), "description": description, "starts_on": starts_on, "wallet_id": wallet_id, "frequency": frequency, "day_of_month": day_of_month, "estimated": estimated, "ends_on": ends_on}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            wallet = self.resolve_wallet(session, wallet_id)
            if starts_on < wallet.opened_on:
                raise DomainError("Programação anterior à abertura da carteira")
            schedule = Schedule(owner_id=self.owner_id, wallet_id=wallet.id, kind=kind, description=description, amount=value, estimated=estimated, frequency=frequency, starts_on=starts_on, day_of_month=(starts_on.day if day_of_month is None else day_of_month) if frequency == "monthly" else None, ends_on=ends_on)
            session.add(schedule)
            session.flush()
            return self._save_command(session, key, payload, {"id": schedule.id, "kind": kind, "amount": str(value), "description": schedule.description, "starts_on": starts_on.isoformat(), "frequency": frequency, "day_of_month": schedule.day_of_month, "wallet_id": wallet.id})

    def list_schedules(self) -> list[dict]:
        with self.factory() as session:
            rows = session.scalars(select(Schedule).where(Schedule.owner_id == self.owner_id).order_by(Schedule.starts_on)).all()
            return [{"id": s.id, "kind": s.kind, "amount": str(s.amount), "description": s.description, "wallet_id": s.wallet_id, "frequency": s.frequency, "starts_on": s.starts_on.isoformat(), "day_of_month": s.day_of_month, "ends_on": s.ends_on.isoformat() if s.ends_on else None, "estimated": s.estimated, "active": s.active} for s in rows]

    def edit_schedule(self, schedule_id: str, *, key: str, effective_on: date, amount: str, description: str, wallet_id: str, frequency: str, day_of_month: int | None, estimated: bool, ends_on: date | None = None) -> dict:
        value = money(amount)
        description = bounded_text(description, 300, "Descrição")
        if frequency not in {"once", "monthly"}:
            raise DomainError("Programação inválida")
        if frequency == "monthly" and not 1 <= (effective_on.day if day_of_month is None else day_of_month) <= 31:
            raise DomainError("Dia do mês inválido")
        if ends_on and ends_on < effective_on:
            raise DomainError("Fim anterior ao início")
        if effective_on < today_local():
            raise DomainError("Edição de programação deve começar hoje ou no futuro")
        payload = {"action": "edit_schedule", "schedule_id": schedule_id, "effective_on": effective_on, "amount": str(value), "description": description, "wallet_id": wallet_id, "frequency": frequency, "day_of_month": day_of_month, "estimated": estimated, "ends_on": ends_on}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            old = session.scalar(select(Schedule).where(Schedule.id == schedule_id, Schedule.owner_id == self.owner_id, Schedule.active.is_(True)))
            if old is None:
                raise DomainError("Programação não encontrada ou já editada")
            wallet = self._wallet(session, wallet_id)
            if effective_on < wallet.opened_on:
                raise DomainError("Data anterior à abertura da carteira")
            self._ensure_schedule_occurrences(session, old, effective_on - timedelta(days=1))
            old.active = False
            pending = session.scalars(select(Occurrence).where(Occurrence.schedule_id == old.id, Occurrence.status == "pending", Occurrence.due_on >= effective_on)).all()
            for occurrence in pending:
                occurrence.status = "cancelled"
            replacement = Schedule(owner_id=self.owner_id, wallet_id=wallet.id, kind=old.kind, description=description, amount=value, estimated=estimated, frequency=frequency, starts_on=effective_on, day_of_month=(effective_on.day if day_of_month is None else day_of_month) if frequency == "monthly" else None, ends_on=ends_on, replaces_id=old.id)
            session.add(replacement)
            session.flush()
            return self._save_command(session, key, payload, {"old_schedule_id": old.id, "new_schedule_id": replacement.id, "cancelled_occurrences": len(pending)})

    def cancel_schedule(self, schedule_id: str, *, key: str) -> dict:
        payload = {"action": "cancel_schedule", "schedule_id": schedule_id}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            schedule = session.scalar(select(Schedule).where(Schedule.id == schedule_id, Schedule.owner_id == self.owner_id, Schedule.active.is_(True)))
            if schedule is None:
                raise DomainError("Programação não encontrada ou já cancelada")
            schedule.active = False
            pending = session.scalars(select(Occurrence).where(Occurrence.schedule_id == schedule.id, Occurrence.status == "pending")).all()
            for occurrence in pending:
                occurrence.status = "cancelled"
            return self._save_command(session, key, payload, {"schedule_id": schedule.id, "cancelled_occurrences": len(pending)})

    def cancel_occurrence(self, occurrence_id: str, *, key: str) -> dict:
        payload = {"action": "cancel_occurrence", "occurrence_id": occurrence_id}
        with self.factory.begin() as session:
            if previous := self._command(session, key, payload):
                return previous
            occurrence = session.get(Occurrence, occurrence_id)
            schedule = session.get(Schedule, occurrence.schedule_id) if occurrence else None
            if schedule is None or schedule.owner_id != self.owner_id or occurrence.status != "pending":
                raise DomainError("Ocorrência não encontrada ou indisponível")
            occurrence.status = "cancelled"
            return self._save_command(session, key, payload, {"occurrence_id": occurrence.id, "status": "cancelled"})

    def _due_dates(self, schedule: Schedule, through: date):
        end = min(through, schedule.ends_on) if schedule.ends_on else through
        if schedule.starts_on > end:
            return
        if schedule.frequency == "once":
            yield schedule.starts_on
            return
        year, month = schedule.starts_on.year, schedule.starts_on.month
        while (year, month) <= (end.year, end.month):
            day = min(schedule.day_of_month or 1, calendar.monthrange(year, month)[1])
            due = date(year, month, day)
            if schedule.starts_on <= due <= end:
                yield due
            month += 1
            if month == 13:
                month, year = 1, year + 1

    def _ensure_schedule_occurrences(self, session: Session, schedule: Schedule, through: date) -> None:
        existing = set(session.scalars(select(Occurrence.due_on).where(Occurrence.schedule_id == schedule.id, Occurrence.due_on <= through)))
        for due in self._due_dates(schedule, through):
            if due not in existing:
                session.add(Occurrence(schedule_id=schedule.id, due_on=due))
        session.flush()

    def _ensure_occurrences(self, session: Session, through: date) -> None:
        schedules = session.scalars(select(Schedule).where(Schedule.owner_id == self.owner_id, Schedule.active.is_(True))).all()
        for schedule in schedules:
            self._ensure_schedule_occurrences(session, schedule, through)

    def list_occurrences(self, *, through: date, status: str | None = None) -> list[dict]:
        if through > today_local() + timedelta(days=3650):
            raise DomainError("Horizonte máximo de dez anos")
        with self.factory.begin() as session:
            self._ensure_occurrences(session, through)
            query = select(Occurrence, Schedule).join(Schedule, Occurrence.schedule_id == Schedule.id).where(Schedule.owner_id == self.owner_id, Occurrence.due_on <= through)
            if status:
                query = query.where(Occurrence.status == status)
            rows = session.execute(query.order_by(Occurrence.due_on)).all()
            return [{"id": o.id, "schedule_id": s.id, "due_on": o.due_on.isoformat(), "status": o.status, "kind": s.kind, "amount": str(s.amount), "actual_amount": str(o.actual_amount) if o.actual_amount is not None else None, "wallet_id": s.wallet_id, "description": s.description, "estimated": s.estimated, "overdue": o.status == "pending" and o.due_on < today_local()} for o, s in rows]

    def projection(self, through: date, *, wallet_ids: list[str] | None = None) -> dict:
        current_day = today_local()
        if through < current_day:
            raise DomainError("A previsão exige data atual ou futura")
        balances = self.balances(wallet_ids=wallet_ids)
        allowed = {item["wallet_id"] for item in balances["wallets"]}
        occurrences = self.list_occurrences(through=through, status="pending")
        projected = {item["wallet_id"]: Decimal(item["balance"]) for item in balances["wallets"]}
        overdue = []
        upcoming = []
        for occurrence in occurrences:
            if occurrence["wallet_id"] not in allowed:
                continue
            if occurrence["overdue"]:
                overdue.append(occurrence)
                continue
            delta = Decimal(occurrence["amount"]) * (1 if occurrence["kind"] == "income" else -1)
            projected[occurrence["wallet_id"]] += delta
            upcoming.append(occurrence)
        return {"through": through.isoformat(), "current": balances, "wallets": [{"wallet_id": item["wallet_id"], "name": item["name"], "projected": str(projected[item["wallet_id"]])} for item in balances["wallets"]], "projected_total": str(sum(projected.values(), Decimal("0.00"))), "upcoming": upcoming, "overdue_excluded": overdue}

    def confirm_occurrence(self, occurrence_id: str, amount: str, occurred_on: date, *, key: str, description: str | None = None) -> dict:
        with self.factory() as session:
            occurrence = session.get(Occurrence, occurrence_id)
            schedule = session.get(Schedule, occurrence.schedule_id) if occurrence else None
            if schedule is None or schedule.owner_id != self.owner_id:
                raise DomainError("Ocorrência não encontrada")
            return self.record(schedule.kind, amount, occurred_on, description or schedule.description, key=key, wallet_id=schedule.wallet_id, occurrence_id=occurrence_id)

    def aggregates(self, start_on: date, end_on: date) -> dict:
        if end_on < start_on:
            raise DomainError("Período inválido")
        with self.factory() as session:
            rows = session.scalars(select(Movement).where(Movement.owner_id == self.owner_id, Movement.voided_at.is_(None), Movement.occurred_on.between(start_on, end_on), Movement.kind.in_(["income", "expense"]))).all()
            income = Decimal("0.00")
            expense = Decimal("0.00")
            categories: dict[str, Decimal] = {}
            for row in rows:
                if row.kind == "income":
                    income += row.amount
                else:
                    expense += row.amount
                    category = row.category or "Outros"
                    categories[category] = categories.get(category, Decimal("0.00")) + row.amount
            return {"start_on": start_on.isoformat(), "end_on": end_on.isoformat(), "income": str(income), "expense": str(expense), "net": str(income - expense), "expense_categories": {name: str(value) for name, value in sorted(categories.items())}}

    def simulate_expense(self, amount: str, occurred_on: date, *, wallet_id: str | None = None) -> dict:
        value = money(amount)
        if occurred_on < today_local():
            raise DomainError("Simulação exige data atual ou futura")
        with self.factory() as session:
            wallet = self.resolve_wallet(session, wallet_id)
            wallet_id = wallet.id
        before = self.projection(occurred_on, wallet_ids=[wallet_id])
        return {"wallet_id": wallet_id, "date": occurred_on.isoformat(), "before": before["projected_total"], "after": str(Decimal(before["projected_total"]) - value), "persisted": False}
