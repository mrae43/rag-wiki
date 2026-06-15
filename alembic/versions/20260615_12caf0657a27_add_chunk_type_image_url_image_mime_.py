"""add chunk_type image_url image_mime_type metadata_ to chunks; bump vector to 3072

Revision ID: 12caf0657a27
Revises: 00cf5e4379c4
Create Date: 2026-06-15 09:09:53.639110

"""
from collections.abc import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '12caf0657a27'
down_revision: str | Sequence[str] | None = '00cf5e4379c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- New columns for chunk type discrimination ---
    op.add_column('chunks', sa.Column('chunk_type', sa.Text(), server_default='text', nullable=False))
    op.add_column('chunks', sa.Column('image_url', sa.Text(), nullable=True))
    op.add_column('chunks', sa.Column('image_mime_type', sa.Text(), nullable=True))
    op.add_column('chunks', sa.Column('metadata_', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.alter_column('chunks', 'text_content',
               existing_type=sa.TEXT(),
               nullable=True)

    # --- HNSW indexes must be dropped before ALTER COLUMN TYPE ---
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_entities_embedding_hnsw")

    # --- Bump vector dimensions to 3072 ---
    op.alter_column('chunks', 'embedding',
               existing_type=Vector(1536),
               type_=Vector(3072),
               postgresql_using='embedding::vector(3072)')
    op.alter_column('entities', 'embedding',
               existing_type=Vector(1536),
               type_=Vector(3072),
               postgresql_using='embedding::vector(3072)')

    # --- Index skipped: pgvector HNSW and IVFFlat both cap at 2000 dimensions.
    #     3072-dim vectors cannot be indexed directly. A future iteration can
    #     use expression indexing (e.g. halfvec cast) or dimensionality reduction.


def downgrade() -> None:
    # --- Revert vector dimensions to 1536 ---
    op.alter_column('entities', 'embedding',
               existing_type=Vector(3072),
               type_=Vector(1536),
               postgresql_using='embedding::vector(1536)')
    op.alter_column('chunks', 'embedding',
               existing_type=Vector(3072),
               type_=Vector(1536),
               postgresql_using='embedding::vector(1536)')

    # --- Recreate HNSW indexes at old dimension ---
    op.execute(
        "CREATE INDEX idx_entities_embedding_hnsw ON entities USING hnsw (embedding vector_cosine_ops) WITH (m = 24, ef_construction = 200)"
    )
    op.execute(
        "CREATE INDEX idx_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 24, ef_construction = 200)"
    )

    op.alter_column('chunks', 'text_content',
               existing_type=sa.TEXT(),
               nullable=False)
    op.drop_column('chunks', 'metadata_')
    op.drop_column('chunks', 'image_mime_type')
    op.drop_column('chunks', 'image_url')
    op.drop_column('chunks', 'chunk_type')
