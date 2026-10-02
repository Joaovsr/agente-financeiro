import pytest

from finance_agent.config import Settings, load_settings, load_storage_settings, load_telegram_token


def test_production_rejects_sqlite_even_with_credentials():
    config = Settings(app_env="production", database_url="sqlite:///./finance.db", telegram_user_id=123, telegram_bot_token="123:real-token", telegram_webhook_secret="a-secret-long-enough", openai_api_key="sk-example")
    with pytest.raises(ValueError, match="PostgreSQL"):
        config.validate_production()


def test_production_rejects_placeholder_secrets():
    config = Settings(app_env="production", database_url="postgresql+psycopg://finance:pass@localhost/finance", telegram_user_id=123, telegram_bot_token="123:substitua", telegram_webhook_secret="a-secret-long-enough", openai_api_key="sk-example")
    with pytest.raises(ValueError, match="credenciais reais"):
        config.validate_production()


def test_production_rejects_compose_development_credentials():
    config = Settings(app_env="production", database_url="postgresql+psycopg://finance:pass@localhost/finance", telegram_user_id=123, telegram_bot_token="123:development-only", telegram_webhook_secret="local-test-secret-123456", openai_api_key="development-only")
    with pytest.raises(ValueError, match="credenciais reais"):
        config.validate_production()


def test_load_settings_uses_configured_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    env_path.write_text("TELEGRAM_USER_ID=456\nTELEGRAM_BOT_TOKEN=123:ABC\nTELEGRAM_WEBHOOK_SECRET=long-secret-value\nOPENAI_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENV_FILE", str(env_path))
    assert load_settings().telegram_user_id == 456


def test_storage_commands_do_not_require_api_credentials(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    env_path.write_text("DATABASE_URL=sqlite:///./test.db\nOWNER_ID=private-owner\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENV_FILE", str(env_path))
    config = load_storage_settings()
    assert config.owner_id == "private-owner"


def test_telegram_id_discovery_requires_only_bot_token(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=123:ABC\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENV_FILE", str(env_path))
    assert load_telegram_token().telegram_bot_token == "123:ABC"


def test_production_storage_commands_reject_sqlite(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    env_path.write_text("APP_ENV=production\nDATABASE_URL=sqlite:///./finance.db\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENV_FILE", str(env_path))
    with pytest.raises(ValueError, match="PostgreSQL"):
        load_storage_settings()
