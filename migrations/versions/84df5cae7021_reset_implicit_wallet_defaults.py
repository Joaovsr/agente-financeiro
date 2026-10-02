"""Clear wallet defaults that may have been assigned implicitly.

Revision ID: 84df5cae7021
Revises: 426d9bcfa356
"""

from alembic import op
import sqlalchemy as sa


revision = "84df5cae7021"
down_revision = "426d9bcfa356"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Earlier code marked the first wallet as default without an owner choice.
    wallets = sa.table("wallets", sa.column("is_default", sa.Boolean()))
    op.execute(wallets.update().where(wallets.c.is_default.is_(True)).values(is_default=False))


def downgrade() -> None:
    # A previous preference cannot be distinguished from an implicit default.
    pass
