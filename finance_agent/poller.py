"""Recebe mensagens do Telegram por polling durante testes locais."""

import argparse
import asyncio
import logging

import httpx
from aiogram import Bot
from aiogram.types import Update

from .config import load_settings

logger = logging.getLogger(__name__)


async def forward_once(bot: Bot, client: httpx.AsyncClient, secret: str, offset: int | None) -> int | None:
    updates = await bot.get_updates(offset=offset, limit=20, timeout=20, allowed_updates=["message"])
    for update in updates:
        response = await client.post(
            "/telegram/webhook",
            content=update.model_dump_json(by_alias=True, exclude_none=True),
            headers={"X-Telegram-Bot-Api-Secret-Token": secret, "Content-Type": "application/json"},
        )
        response.raise_for_status()
        offset = update.update_id + 1
        logger.info("Atualização %s processada", update.update_id)
    return offset


async def run(*, skip_pending: bool = False, api_port: int = 8000) -> None:
    settings = load_settings()
    if settings.app_env != "development":
        raise ValueError("Polling local só pode ser usado em desenvolvimento")
    if not 1 <= api_port <= 65535:
        raise ValueError("Porta da API inválida")
    bot = Bot(token=settings.telegram_bot_token)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=30) as client:
            offset = None
            if skip_pending:
                pending = await bot.get_updates(limit=100, timeout=0, allowed_updates=["message"])
                if pending:
                    offset = max(update.update_id for update in pending) + 1
                    logger.info("Ignoradas %s atualizações anteriores à inicialização", len(pending))
            logger.info("Aguardando mensagens privadas do Telegram para a API local")
            while True:
                try:
                    offset = await forward_once(bot, client, settings.telegram_webhook_secret, offset)
                except Exception:
                    logger.exception("Falha no polling ou no encaminhamento; nova tentativa em 5 segundos")
                    await asyncio.sleep(5)
    finally:
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Encaminha mensagens do Telegram à API local")
    parser.add_argument("--skip-pending", action="store_true", help="Ignora mensagens anteriores à inicialização")
    parser.add_argument("--api-port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(skip_pending=args.skip_pending, api_port=args.api_port))


if __name__ == "__main__":
    main()
