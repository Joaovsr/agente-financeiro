"""Verificações locais de prontidão do esquema financeiro."""

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config import PROJECT_ROOT


def expected_revision() -> str:
    return ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini"))).get_current_head()


def schema_issue(factory: sessionmaker[Session], head: str) -> str | None:
    with factory() as session:
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return "Banco indisponível"
        try:
            revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
        except SQLAlchemyError:
            return "Esquema indisponível"
    if revisions != [head]:
        return "Migrações pendentes"
    return None
