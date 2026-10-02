"""Comandos de operação local do agente financeiro."""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings, StorageSettings, load_settings, load_storage_settings, load_telegram_token
from .database import make_session_factory
from .models import Command, ImportBatch, IncomingEvent, Investment, Movement, Notification, NotificationSettings, Occurrence, Schedule, Wallet
from .notifications import NotificationService
from .readiness import expected_revision, schema_issue
from .service import FinanceService


def export_data(config: Settings | StorageSettings, destination: Path) -> None:
    factory = make_session_factory(config.database_url)
    with factory() as session:
        wallets = session.scalars(select(Wallet).where(Wallet.owner_id == config.owner_id)).all()
        schedules = session.scalars(select(Schedule).where(Schedule.owner_id == config.owner_id)).all()
        schedule_ids = [row.id for row in schedules]
        data = {
            "format_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "owner_id": config.owner_id,
            "wallets": [_row(row) for row in wallets],
            "movements": [_row(row) for row in session.scalars(select(Movement).where(Movement.owner_id == config.owner_id)).all()],
            "schedules": [_row(row) for row in schedules],
            "occurrences": [_row(row) for row in session.scalars(select(Occurrence).where(Occurrence.schedule_id.in_(schedule_ids))).all()] if schedule_ids else [],
            "investments": [_row(row) for row in session.scalars(select(Investment).where(Investment.owner_id == config.owner_id)).all()],
            "import_batches": [_row(row) for row in session.scalars(select(ImportBatch).where(ImportBatch.owner_id == config.owner_id)).all()],
            "notification_settings": [_row(row) for row in session.scalars(select(NotificationSettings).where(NotificationSettings.owner_id == config.owner_id)).all()],
            "notifications": [_row(row) for row in session.scalars(select(Notification).where(Notification.owner_id == config.owner_id)).all()],
            "commands": [_row(row) for row in session.scalars(select(Command).where(Command.owner_id == config.owner_id)).all()],
            "conversations": [_row(row) for row in session.scalars(select(IncomingEvent).where(IncomingEvent.owner_id == config.owner_id)).all()],
        }
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(payload)


def _row(model) -> dict:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


def preflight(config: Settings) -> None:
    """Confere configuração e esquema locais antes do piloto, sem testar serviços externos."""
    if config.app_env != "production":
        raise ValueError("Defina APP_ENV=production para verificar o piloto")
    config.validate_production()
    try:
        factory = make_session_factory(config.database_url)
    except SQLAlchemyError as exc:
        raise RuntimeError("Banco indisponível") from exc
    try:
        if issue := schema_issue(factory, expected_revision()):
            raise RuntimeError(issue)
    finally:
        factory.kw["bind"].dispose()


async def set_webhook(config: Settings, public_url: str) -> None:
    if not public_url.startswith("https://"):
        raise ValueError("A URL pública do webhook deve usar HTTPS")
    bot = Bot(token=config.telegram_bot_token)
    try:
        await bot.set_webhook(public_url.rstrip("/") + "/telegram/webhook", secret_token=config.telegram_webhook_secret, allowed_updates=["message"], max_connections=1)
    finally:
        await bot.session.close()


async def discover_private_user_ids(token: str) -> list[int]:
    bot = Bot(token=token)
    try:
        updates = await bot.get_updates(limit=20, timeout=0)
        return sorted({update.message.from_user.id for update in updates if update.message and update.message.from_user and update.message.chat.type == "private"})
    finally:
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Administração do agente financeiro")
    commands = parser.add_subparsers(dest="command", required=True)
    webhook_parser = commands.add_parser("set-webhook", help="Registra a URL pública no Telegram")
    webhook_parser.add_argument("url")
    commands.add_parser("discover-telegram-id", help="Mostra IDs de usuários que enviaram mensagem privada ao bot antes do webhook")
    commands.add_parser("preflight", help="Confere configuração de produção e migrações sem chamar serviços externos")
    export_parser = commands.add_parser("export", help="Exporta os dados do dono em JSON")
    export_parser.add_argument("destination", type=Path)
    retry_parser = commands.add_parser("retry-notification", help="Autoriza nova tentativa de envio incerto após conferir no Telegram")
    retry_parser.add_argument("notification_id")
    list_parser = commands.add_parser("list-notifications", help="Lista avisos para revisão operacional")
    list_parser.add_argument("--status", choices=["pending", "retry_pending", "sending", "sent", "uncertain"], default="uncertain")
    args = parser.parse_args()
    if args.command == "set-webhook":
        asyncio.run(set_webhook(load_settings(), args.url))
    elif args.command == "discover-telegram-id":
        for user_id in asyncio.run(discover_private_user_ids(load_telegram_token().telegram_bot_token)):
            print(user_id)
    elif args.command == "preflight":
        try:
            preflight(load_settings())
        except (ValueError, RuntimeError) as exc:
            parser.exit(1, f"Pré-verificação falhou: {exc}\n")
        print("Configuração local e migrações prontas. Ainda valide OpenAI, Telegram, HTTPS e restauração do backup.")
    elif args.command == "export":
        export_data(load_storage_settings(), args.destination)
    elif args.command in {"retry-notification", "list-notifications"}:
        config = load_storage_settings()
        factory = make_session_factory(config.database_url)
        service = NotificationService(FinanceService(factory, config.owner_id))
        try:
            if args.command == "retry-notification":
                if not service.retry_uncertain(args.notification_id):
                    raise ValueError("Notificação incerta ou em envio não encontrada")
            else:
                for row in service.list_notifications(status=args.status):
                    print(f"{row['id']} {row['status']} {row['event_key']} tentativas={row['attempts']}")
        finally:
            factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
