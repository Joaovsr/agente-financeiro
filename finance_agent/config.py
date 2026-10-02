import os
from typing import Literal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def env_file() -> Path:
    return Path(os.environ.get("AGENT_ENV_FILE", PROJECT_ROOT / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "production"] = "development"
    database_url: str = "sqlite:///./finance.db"
    owner_id: str = "owner"
    telegram_user_id: int
    telegram_bot_token: str
    telegram_webhook_secret: str
    openai_api_key: str
    openai_model: str = "gpt-5.6"
    transcription_model: str = "gpt-transcribe"

    def validate_production(self) -> None:
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Use PostgreSQL para operação com dados reais")
        if not self.telegram_webhook_secret or len(self.telegram_webhook_secret) < 16:
            raise ValueError("Defina um segredo de webhook com ao menos 16 caracteres")
        placeholders = ("substitua", "development-only")
        if self.telegram_user_id <= 0 or any(marker in value.lower() for value in (self.telegram_bot_token, self.openai_api_key, self.telegram_webhook_secret) for marker in placeholders):
            raise ValueError("Configure credenciais reais antes de iniciar a produção")


def load_settings() -> Settings:
    settings = Settings(_env_file=env_file())
    if settings.app_env == "production":
        settings.validate_production()
    return settings


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "production"] = "development"
    database_url: str = "sqlite:///./finance.db"
    owner_id: str = "owner"

    def validate_production(self) -> None:
        if self.app_env == "production" and not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Use PostgreSQL para operação com dados reais")


def load_storage_settings() -> StorageSettings:
    settings = StorageSettings(_env_file=env_file())
    settings.validate_production()
    return settings


class TelegramTokenSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram_bot_token: str


def load_telegram_token() -> TelegramTokenSettings:
    return TelegramTokenSettings(_env_file=env_file())


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    openai_api_key: str
    openai_model: str = "gpt-5.6"


def load_ai_settings() -> AISettings:
    return AISettings(_env_file=env_file())
