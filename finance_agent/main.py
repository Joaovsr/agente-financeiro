"""Webhook FastAPI com adaptação aiogram e autorização pelo ID do Telegram."""

import hmac
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy import select

from .agent import FinancialAgent
from .config import Settings, load_settings
from .database import make_session_factory
from .models import Command, IncomingEvent
from .readiness import expected_revision, schema_issue
from .service import DomainError, FinanceService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, bot: Bot | None = None, ai_client: AsyncOpenAI | None = None) -> FastAPI:
    config = settings or load_settings()
    if config.app_env == "production":
        config.validate_production()
    factory = make_session_factory(config.database_url)
    schema_head = expected_revision()
    own_bot = bot is None
    telegram = bot or Bot(token=config.telegram_bot_token)
    agent = FinancialAgent(FinanceService(factory, config.owner_id), model=config.openai_model, client=ai_client or AsyncOpenAI(api_key=config.openai_api_key), transcription_model=config.transcription_model)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if own_bot:
            await telegram.session.close()
        await agent.client.close()
        factory.kw["bind"].dispose()

    app = FastAPI(title="Agente Financeiro", lifespan=lifespan)
    app.state.settings = config
    app.state.agent = agent
    app.state.factory = factory
    app.state.webhook_lock = asyncio.Lock()

    @app.middleware("http")
    async def serialize_webhook(request: Request, call_next):
        if request.url.path == "/telegram/webhook":
            async with app.state.webhook_lock:
                return await call_next(request)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz():
        if issue := schema_issue(factory, schema_head):
            raise HTTPException(status_code=503, detail=issue)
        return {"ok": True}

    @app.post("/telegram/webhook")
    async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        if not x_telegram_bot_api_secret_token or not hmac.compare_digest(x_telegram_bot_api_secret_token, config.telegram_webhook_secret):
            raise HTTPException(status_code=403, detail="Webhook não autorizado")
        if config.app_env == "production":
            await readyz()
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise HTTPException(status_code=413, detail="Atualização do Telegram grande demais")
        try:
            raw = json.loads(body)
            update = Update.model_validate(raw, context={"bot": telegram})
        except (json.JSONDecodeError, ValidationError):
            raise HTTPException(status_code=400, detail="Atualização do Telegram inválida")
        message = update.message
        if message is None or message.from_user is None:
            return {"ok": True}
        if message.chat.type != "private" or message.from_user.id != config.telegram_user_id or message.chat.id != config.telegram_user_id:
            return {"ok": True}
        has_attachment = bool(message.photo or message.document)
        if not message.text and not message.voice and not has_attachment:
            await telegram.send_message(message.chat.id, "Envie texto, áudio, imagem ou PDF.")
            return {"ok": True}

        event_id = str(update.update_id)
        with factory() as session:
            event = session.scalar(select(IncomingEvent).where(IncomingEvent.owner_id == config.owner_id, IncomingEvent.channel == "telegram", IncomingEvent.event_id == event_id))
            event_exists = event is not None
            response_text = event.response_text if event else None
            already_sent = event.sent_at is not None if event else False
            user_text = event.user_text if event else None
        if already_sent:
            return {"ok": True}
        if response_text is None and event_exists:
            prefix = f"telegram:{event_id}:write:"
            with factory() as session:
                committed = session.scalars(select(Command).where(Command.owner_id == config.owner_id, Command.key.like(prefix + "%"))).all()
            committed = [command for command in committed if command.key.startswith(prefix) and command.key[len(prefix):].isdecimal()]
            if committed:
                committed.sort(key=lambda command: int(command.key.removeprefix(prefix)))
                details = [agent.describe_committed_write(command.action, command.result) for command in committed]
                response_text = ("Recuperei uma operação já gravada após uma interrupção:\n" if len(details) == 1 else "Recuperei operações já gravadas após uma interrupção:\n") + "\n".join(details)
                response_text += "\nNão executei novamente o pedido; consulte os registros antes de solicitar qualquer parte restante."
                response_text = response_text[:3900]
                with factory.begin() as session:
                    event = session.scalar(select(IncomingEvent).where(IncomingEvent.owner_id == config.owner_id, IncomingEvent.channel == "telegram", IncomingEvent.event_id == event_id))
                    event.response_text = response_text
        if response_text is None:
            preparation_error = None
            if user_text is None:
                if has_attachment:
                    user_text = f"[Documento recebido: {message.document.file_name if message.document else 'foto.jpg'}]"
                elif message.voice:
                    if message.voice.file_size and message.voice.file_size > 20 * 1024 * 1024:
                        user_text = "[Áudio acima de 20 MB]"
                        preparation_error = "Áudio acima de 20 MB. Envie uma gravação menor."
                    else:
                        try:
                            downloaded = await telegram.download(message.voice)
                            user_text = await agent.transcribe(downloaded.getvalue())
                        except DomainError as exc:
                            user_text = "[Áudio não processado]"
                            preparation_error = str(exc)
                        except Exception:
                            logger.exception("Falha ao transcrever áudio")
                            raise HTTPException(status_code=503, detail="Não foi possível transcrever o áudio")
                else:
                    user_text = message.text
                    if len(user_text) > 10000:
                        user_text = "[Mensagem acima de 10 mil caracteres]"
                        preparation_error = "Mensagem longa demais; divida o pedido."
                if len(user_text) > 10000:
                    user_text = "[Documento com nome longo demais]"
                    preparation_error = "Nome do documento longo demais; renomeie o arquivo."
                with factory.begin() as session:
                    session.add(IncomingEvent(owner_id=config.owner_id, channel="telegram", event_id=event_id, user_text=user_text))
            with factory() as session:
                previous = session.scalars(select(IncomingEvent).where(IncomingEvent.owner_id == config.owner_id, IncomingEvent.channel == "telegram", IncomingEvent.response_text.is_not(None), IncomingEvent.event_id != event_id).order_by(IncomingEvent.created_at.desc()).limit(6)).all()
                history = []
                for item in reversed(previous):
                    history.extend([{"role": "user", "content": item.user_text}, {"role": "assistant", "content": item.response_text}])
            try:
                if preparation_error:
                    response_text = preparation_error
                elif has_attachment:
                    media = message.document or max(message.photo, key=lambda photo: photo.file_size or photo.width * photo.height)
                    if media.file_size and media.file_size > 10 * 1024 * 1024:
                        raise DomainError("Documento acima de 10 MB. Envie um arquivo menor.")
                    filename = message.document.file_name or "documento" if message.document else "foto.jpg"
                    suffix = filename.lower().rsplit(".", 1)[-1]
                    content_type = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "")
                    if not content_type:
                        raise DomainError("Envie imagem JPG, PNG, WEBP ou PDF")
                    downloaded = await telegram.download(media)
                    batch = await agent.import_document(downloaded.getvalue(), filename, content_type)
                    response_text = agent.describe_batch(batch)
                else:
                    response_text = await agent.reply(user_text, event_key=f"telegram:{event_id}", history=history)
                    if message.voice:
                        response_text = f"Entendi: {user_text}\n{response_text}"
                response_text = response_text[:3900]
            except DomainError as exc:
                response_text = str(exc)
            except HTTPException:
                raise
            except Exception:
                logger.exception("Falha ao processar mensagem financeira")
                raise HTTPException(status_code=503, detail="Não foi possível processar a mensagem")
            with factory.begin() as session:
                event = session.scalar(select(IncomingEvent).where(IncomingEvent.owner_id == config.owner_id, IncomingEvent.channel == "telegram", IncomingEvent.event_id == event_id))
                event.response_text = response_text
        await telegram.send_message(message.chat.id, response_text)
        with factory.begin() as session:
            event = session.scalar(select(IncomingEvent).where(IncomingEvent.owner_id == config.owner_id, IncomingEvent.channel == "telegram", IncomingEvent.event_id == event_id))
            event.sent_at = datetime.now(timezone.utc)
        return {"ok": True}

    return app
