"""Processo separado para enviar avisos; execute com --once ou em loop."""

import argparse
import asyncio
import logging

from aiogram import Bot

from .config import load_settings
from .database import make_session_factory
from .notifications import NotificationService, send_prepared
from .service import FinanceService

logger = logging.getLogger(__name__)


async def run(once: bool) -> None:
    config = load_settings()
    factory = make_session_factory(config.database_url)
    service = NotificationService(FinanceService(factory, config.owner_id))
    bot = Bot(token=config.telegram_bot_token)
    try:
        while True:
            try:
                count = await send_prepared(service, bot, config.telegram_user_id)
                logger.info("Avisos enviados: %s", count)
            except Exception:
                logger.exception("Falha no envio de avisos")
            if once:
                break
            await asyncio.sleep(3600)
    finally:
        await bot.session.close()
        factory.kw["bind"].dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Executa uma rodada e termina")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.once))
