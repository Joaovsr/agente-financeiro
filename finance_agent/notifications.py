"""Preferências e geração de avisos sem alterar movimentos financeiros."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, or_, select, update

from .models import Notification, NotificationSettings
from .service import DomainError, FinanceService, brl, today_local


class NotificationService:
    def __init__(self, finance: FinanceService):
        self.finance = finance

    def get_settings(self) -> dict:
        with self.finance.factory() as session:
            settings = session.get(NotificationSettings, self.finance.owner_id)
            if settings is None:
                return {"enabled": False, "reminder_days_before": 1, "negative_alert": True, "monthly_summary": True}
            return {"enabled": settings.enabled, "reminder_days_before": settings.reminder_days_before, "negative_alert": settings.negative_alert, "monthly_summary": settings.monthly_summary}

    def configure(self, *, enabled: bool, reminder_days_before: int, negative_alert: bool, monthly_summary: bool, key: str) -> dict:
        if not 0 <= reminder_days_before <= 30:
            raise DomainError("Antecedência deve estar entre 0 e 30 dias")
        payload = {"action": "configure_notifications", "enabled": enabled, "reminder_days_before": reminder_days_before, "negative_alert": negative_alert, "monthly_summary": monthly_summary}
        with self.finance.factory.begin() as session:
            if previous := self.finance._command(session, key, payload):
                return previous
            settings = session.get(NotificationSettings, self.finance.owner_id)
            if settings is None:
                settings = NotificationSettings(owner_id=self.finance.owner_id)
                session.add(settings)
            settings.enabled = enabled
            settings.reminder_days_before = reminder_days_before
            settings.negative_alert = negative_alert
            settings.monthly_summary = monthly_summary
            return self.finance._save_command(session, key, payload, {"enabled": enabled, "reminder_days_before": reminder_days_before, "negative_alert": negative_alert, "monthly_summary": monthly_summary})

    def prepare(self, *, on_date: date | None = None) -> list[dict]:
        day = on_date or today_local()
        settings = self.get_settings()
        if not settings["enabled"]:
            return []
        candidates: list[tuple[str, str]] = []
        through = day + timedelta(days=settings["reminder_days_before"])
        for occurrence in self.finance.list_occurrences(through=through, status="pending"):
            due = date.fromisoformat(occurrence["due_on"])
            if day <= due <= through:
                candidates.append((f"reminder:{occurrence['id']}:{due}", f"Lembrete: {occurrence['description']} de {brl(Decimal(occurrence['amount']))} em {due.isoformat()}. Confirme quando ocorrer."))
        if settings["negative_alert"]:
            forecast = self.finance.projection(day + timedelta(days=31))
            balances = {w["wallet_id"]: Decimal(w["balance"]) for w in forecast["current"]["wallets"]}
            names = {w["wallet_id"]: w["name"] for w in forecast["current"]["wallets"]}
            for wallet_id, balance in balances.items():
                if balance < 0:
                    candidates.append((f"negative:{wallet_id}:current:{day.year}-{day.month:02d}", f"Alerta: a carteira {names[wallet_id]} está com saldo de {brl(balance)}."))
            effects: dict[tuple[str, str], Decimal] = {}
            contributors: dict[tuple[str, str], list[str]] = {}
            for occurrence in forecast["upcoming"]:
                slot = (occurrence["due_on"], occurrence["wallet_id"])
                effects[slot] = effects.get(slot, Decimal("0.00")) + Decimal(occurrence["amount"]) * (1 if occurrence["kind"] == "income" else -1)
                contributors.setdefault(slot, []).append(occurrence["description"])
            for (due, wallet_id), effect in sorted(effects.items()):
                old_balance = balances[wallet_id]
                balances[wallet_id] += effect
                if old_balance >= 0 and balances[wallet_id] < 0:
                    detail = ", ".join(contributors[(due, wallet_id)][:3])
                    candidates.append((f"negative:{wallet_id}:{due}", f"Alerta: a carteira {names[wallet_id]} pode chegar a {brl(balances[wallet_id])} em {due}. Compromissos: {detail}."))
        if settings["monthly_summary"] and day.day <= 7:
            end = date(day.year, day.month, 1) - timedelta(days=1)
            start = date(end.year, end.month, 1)
            summary = self.finance.aggregates(start, end)
            candidates.append((f"monthly:{end.year}-{end.month:02d}", f"Resumo de {end.month:02d}/{end.year}: entradas {brl(Decimal(summary['income']))}, gastos {brl(Decimal(summary['expense']))}, resultado {brl(Decimal(summary['net']))}."))
        keys = [key for key, _ in candidates]
        with self.finance.factory.begin() as session:
            for key, text in candidates:
                row = session.scalar(select(Notification).where(Notification.owner_id == self.finance.owner_id, Notification.event_key == key))
                if row is None:
                    session.add(Notification(owner_id=self.finance.owner_id, event_key=key, text=text))
                elif row.status == "pending":
                    row.text = text
            session.flush()
            rows = session.scalars(select(Notification).where(Notification.owner_id == self.finance.owner_id, or_(Notification.status == "retry_pending", and_(Notification.status == "pending", Notification.event_key.in_(keys)))).order_by(Notification.created_at)).all()
            return [{"id": row.id, "event_key": row.event_key, "text": row.text} for row in rows]

    def mark_sending(self, notification_id: str) -> bool:
        with self.finance.factory.begin() as session:
            result = session.execute(update(Notification).where(Notification.id == notification_id, Notification.owner_id == self.finance.owner_id, Notification.status.in_(("pending", "retry_pending"))).values(status="sending", attempts=Notification.attempts + 1))
            return result.rowcount == 1

    def mark_sent(self, notification_id: str) -> None:
        with self.finance.factory.begin() as session:
            row = session.scalar(select(Notification).where(Notification.id == notification_id, Notification.owner_id == self.finance.owner_id, Notification.status == "sending"))
            if row is None:
                raise DomainError("Notificação indisponível")
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)

    def mark_failed(self, notification_id: str) -> None:
        with self.finance.factory.begin() as session:
            row = session.scalar(select(Notification).where(Notification.id == notification_id, Notification.owner_id == self.finance.owner_id, Notification.status == "sending"))
            if row:
                row.status = "uncertain"

    def retry_uncertain(self, notification_id: str) -> bool:
        """Operador deve verificar no Telegram antes de autorizar nova tentativa."""
        with self.finance.factory.begin() as session:
            result = session.execute(update(Notification).where(Notification.id == notification_id, Notification.owner_id == self.finance.owner_id, Notification.status.in_(("uncertain", "sending"))).values(status="retry_pending"))
            return result.rowcount == 1

    def list_notifications(self, *, status: str = "uncertain", limit: int = 50) -> list[dict]:
        if status not in {"pending", "retry_pending", "sending", "sent", "uncertain"} or not 1 <= limit <= 100:
            raise DomainError("Filtro de notificações inválido")
        with self.finance.factory() as session:
            rows = session.scalars(select(Notification).where(Notification.owner_id == self.finance.owner_id, Notification.status == status).order_by(Notification.created_at.desc()).limit(limit)).all()
            return [{"id": row.id, "status": row.status, "event_key": row.event_key, "attempts": row.attempts, "created_at": row.created_at.isoformat()} for row in rows]


async def send_prepared(service: NotificationService, bot, telegram_user_id: int, *, on_date: date | None = None) -> int:
    sent = 0
    for notification in service.prepare(on_date=on_date):
        if not service.mark_sending(notification["id"]):
            continue
        try:
            await bot.send_message(telegram_user_id, notification["text"])
        except Exception:
            service.mark_failed(notification["id"])
            raise
        service.mark_sent(notification["id"])
        sent += 1
    return sent
