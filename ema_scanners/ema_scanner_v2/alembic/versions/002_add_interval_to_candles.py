"""Add interval column to candles table — supports 15m, 1h, 2h, 4h, 6h

Revision ID: 002
Revises: 001
Create Date: 2026-06-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add interval column — default "1h" so all existing rows get a value
    op.add_column(
        "candles",
        sa.Column("interval", sa.String(5), nullable=False, server_default="1h"),
    )

    # 2. Drop old unique constraint (symbol, open_time) — no interval
    op.drop_constraint("uq_candle_symbol_open_time", "candles", type_="unique")

    # 3. Drop old index
    op.drop_index("ix_candle_symbol_open_time", table_name="candles")

    # 4. Create new unique constraint including interval
    op.create_unique_constraint(
        "uq_candle_symbol_interval_open_time",
        "candles",
        ["symbol", "interval", "open_time"],
    )

    # 5. Create new index including interval
    op.create_index(
        "ix_candle_symbol_interval_open_time",
        "candles",
        ["symbol", "interval", "open_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_candle_symbol_interval_open_time", table_name="candles")
    op.drop_constraint("uq_candle_symbol_interval_open_time", "candles", type_="unique")
    op.create_index("ix_candle_symbol_open_time", "candles", ["symbol", "open_time"])
    op.create_unique_constraint(
        "uq_candle_symbol_open_time", "candles", ["symbol", "open_time"]
    )
    op.drop_column("candles", "interval")