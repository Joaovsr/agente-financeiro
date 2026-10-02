"""Store the command action so webhook retries can describe committed writes.

Revision ID: c91d5b73e0a2
Revises: 84df5cae7021
"""

from alembic import op
import sqlalchemy as sa


revision = "c91d5b73e0a2"
down_revision = "84df5cae7021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("commands", sa.Column("action", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("commands", "action")
