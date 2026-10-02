from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id() -> str:
    return uuid4().hex


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(100))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    opened_on: Mapped[date] = mapped_column(Date)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Investment(Base):
    __tablename__ = "investments"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(100))
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    valued_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"))
    kind: Mapped[str] = mapped_column(String(12))  # income or expense
    description: Mapped[str] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    frequency: Mapped[str] = mapped_column(String(12))  # once or monthly
    starts_on: Mapped[date] = mapped_column(Date)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    replaces_id: Mapped[str | None] = mapped_column(ForeignKey("schedules.id", name="fk_schedules_replaces_id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Occurrence(Base):
    __tablename__ = "occurrences"
    __table_args__ = (UniqueConstraint("schedule_id", "due_on"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id"), index=True)
    due_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class Movement(Base):
    __tablename__ = "movements"
    __table_args__ = (UniqueConstraint("occurrence_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # income, expense, transfer, contribution, withdrawal, adjustment
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"))
    destination_wallet_id: Mapped[str | None] = mapped_column(ForeignKey("wallets.id"), nullable=True)
    investment_id: Mapped[str | None] = mapped_column(ForeignKey("investments.id"), nullable=True)
    occurrence_id: Mapped[str | None] = mapped_column(ForeignKey("occurrences.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    occurred_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="conversation")
    replaces_id: Mapped[str | None] = mapped_column(ForeignKey("movements.id"), nullable=True)
    prior_occurrence_id: Mapped[str | None] = mapped_column(ForeignKey("occurrences.id", name="fk_movements_prior_occurrence_id"), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (UniqueConstraint("owner_id", "key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    key: Mapped[str] = mapped_column(String(180))
    action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncomingEvent(Base):
    __tablename__ = "incoming_events"
    __table_args__ = (UniqueConstraint("owner_id", "channel", "event_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(100))
    user_text: Mapped[str] = mapped_column(String(10000))
    response_text: Mapped[str | None] = mapped_column(String(10000), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (UniqueConstraint("owner_id", "file_sha256"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    file_sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    proposals: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    owner_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_days_before: Mapped[int] = mapped_column(Integer, default=1)
    negative_alert: Mapped[bool] = mapped_column(Boolean, default=True)
    monthly_summary: Mapped[bool] = mapped_column(Boolean, default=True)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("owner_id", "event_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    event_key: Mapped[str] = mapped_column(String(180))
    text: Mapped[str] = mapped_column(String(4000))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
