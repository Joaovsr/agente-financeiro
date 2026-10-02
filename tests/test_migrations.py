from pathlib import Path
from io import StringIO

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from finance_agent.database import connection_args


def test_postgresql_connections_have_finite_timeout():
    assert connection_args("postgresql+psycopg://user:password@localhost/db") == {"connect_timeout": 10}
    assert connection_args("sqlite:///:memory:") == {}


def test_migrations_apply_from_empty_database(tmp_path, monkeypatch):
    database_url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert {"wallets", "movements", "schedules", "occurrences", "import_batches", "notifications", "commands"} <= tables


def test_postgresql_migrations_generate_sql_without_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://finance:p%40ss@localhost:5432/finance")
    output = StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE wallets" in sql
    assert "CREATE TABLE notifications" in sql


def test_migrations_read_database_url_from_env_file(tmp_path, monkeypatch):
    database_url = "sqlite:///" + (tmp_path / "configured.db").as_posix()
    env_path = tmp_path / "agent.env"
    env_path.write_text(f"DATABASE_URL={database_url}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENV_FILE", str(env_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    assert "wallets" in inspect(create_engine(database_url)).get_table_names()


def test_production_migrations_refuse_sqlite_fallback(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    env_path.write_text("APP_ENV=production\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENV_FILE", str(env_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(ValueError, match="PostgreSQL"):
        command.upgrade(config, "head")
