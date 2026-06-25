"""rename file_path to storage_key

Revision ID: 7222d58def98
Revises: 4bd96e67635d
Create Date: 2026-06-25 05:56:37.877211

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7222d58def98'
down_revision: str | Sequence[str] | None = '4bd96e67635d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('sources', sa.Column('storage_key', sa.Text(), nullable=False))
    op.execute("UPDATE sources SET storage_key = file_path")
    op.alter_column('sources', 'storage_key', existing_type=sa.Text(), nullable=False)
    op.drop_column('sources', 'file_path')


def downgrade() -> None:
    op.add_column('sources', sa.Column('file_path', sa.Text(), nullable=False))
    op.execute("UPDATE sources SET file_path = storage_key")
    op.alter_column('sources', 'file_path', existing_type=sa.Text(), nullable=False)
    op.drop_column('sources', 'storage_key')
