"""widen vector columns from 2048 to 3072 for gemini-embedding-2

Revision ID: 4f8a3b2c9d1e
Revises: 7222d58def98
Create Date: 2026-07-01 00:00:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "4f8a3b2c9d1e"
down_revision: str | Sequence[str] | None = "7222d58def98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TARGET_DIM = 3072
"""Target vector dimension for all embedding columns."""


def _column_actual_dim(table: str) -> int:
    """Return the dimension of the first non-null vector in *table*.embedding,
    falling back to the column type's declared dimension if the table is empty.
    Returns 0 if undetermined.
    """
    conn = op.get_bind()
    row = conn.execute(
        text(f"SELECT vector_dims(embedding) FROM {table} LIMIT 1")
    ).fetchone()
    if row and row[0]:
        return int(row[0])
    typmod_row = conn.execute(
        text(
            f"SELECT atttypmod FROM pg_attribute "
            f"WHERE attrelid = '{table}'::regclass "
            f"AND attname = 'embedding'"
        )
    ).fetchone()
    if typmod_row and (val := typmod_row[0]) is not None and val != -1:
        return (int(val) - 1) // 4
    return 0


def upgrade() -> None:
    # 1. Drop HNSW indexes (pgvector caps HNSW at 2000 dims; 3072 exceeds it).
    op.execute("DROP INDEX IF EXISTS idx_entities_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_hnsw")

    # 2. Widen each table only if its embedding column is below the target.
    for table in ("entities", "chunks"):
        cur = _column_actual_dim(table)
        if cur >= TARGET_DIM:
            continue
        padding = TARGET_DIM - cur
        if padding <= 0:
            raise ValueError(
                f"Cannot widen {table}.embedding from {cur} to {TARGET_DIM} "
                f"(padding={padding})"
            )
        op.execute(f"ALTER TABLE {table} ADD COLUMN embedding_new vector({TARGET_DIM})")
        op.execute(
            f"UPDATE {table} "
            f"SET embedding_new = array_cat(embedding::float4[], "
            f"array_fill(0::float4, ARRAY[{padding}]))::vector({TARGET_DIM})"
        )
        op.execute(f"ALTER TABLE {table} DROP COLUMN embedding")
        op.execute(f"ALTER TABLE {table} RENAME COLUMN embedding_new TO embedding")


def downgrade() -> None:
    SHRINK_TO = 2048
    for table in ("entities", "chunks"):
        cur = _column_actual_dim(table)
        if cur <= SHRINK_TO:
            continue
        op.execute(f"ALTER TABLE {table} ADD COLUMN embedding_new vector({SHRINK_TO})")
        op.execute(f"UPDATE {table} SET embedding_new = embedding::vector({SHRINK_TO})")
        op.execute(f"ALTER TABLE {table} DROP COLUMN embedding")
        op.execute(f"ALTER TABLE {table} RENAME COLUMN embedding_new TO embedding")
