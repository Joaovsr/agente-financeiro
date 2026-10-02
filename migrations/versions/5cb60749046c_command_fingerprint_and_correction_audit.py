"""command fingerprint and correction audit

Revision ID: 5cb60749046c
Revises: ef82e8a3847d
Create Date: 2026-09-23 14:55:58.970198

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cb60749046c'
down_revision: Union[str, Sequence[str], None] = 'ef82e8a3847d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing commands cannot be safely replayed after this migration.
    with op.batch_alter_table('commands') as batch:
        batch.add_column(sa.Column('request_hash', sa.String(length=64), nullable=False, server_default='legacy'))
    with op.batch_alter_table('movements') as batch:
        batch.add_column(sa.Column('prior_occurrence_id', sa.String(length=32), nullable=True))
        batch.create_foreign_key('fk_movements_prior_occurrence_id', 'occurrences', ['prior_occurrence_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('movements') as batch:
        batch.drop_constraint('fk_movements_prior_occurrence_id', type_='foreignkey')
        batch.drop_column('prior_occurrence_id')
    with op.batch_alter_table('commands') as batch:
        batch.drop_column('request_hash')
