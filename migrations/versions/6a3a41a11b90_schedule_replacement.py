"""schedule replacement

Revision ID: 6a3a41a11b90
Revises: 377f949feb7b
Create Date: 2026-09-23 15:05:08.280551

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a3a41a11b90'
down_revision: Union[str, Sequence[str], None] = '377f949feb7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('schedules') as batch:
        batch.add_column(sa.Column('replaces_id', sa.String(length=32), nullable=True))
        batch.create_foreign_key('fk_schedules_replaces_id', 'schedules', ['replaces_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('schedules') as batch:
        batch.drop_constraint('fk_schedules_replaces_id', type_='foreignkey')
        batch.drop_column('replaces_id')
