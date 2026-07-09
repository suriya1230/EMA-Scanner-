"""Initial schema — candles and signals tables

Revision ID: 001
Revises: 
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── candles ──────────────────────────────────────────────────────────────
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("open_time", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("close_time", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "open_time", name="uq_candle_symbol_open_time"),
    )
    op.create_index("ix_candle_symbol_open_time", "candles", ["symbol", "open_time"])
    op.create_index("ix_candles_symbol", "candles", ["symbol"])

    # ── signals ───────────────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("signal_type", sa.String(4), nullable=False),
        sa.Column("cross_price", sa.Float(), nullable=False),
        sa.Column("cross_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ema_7", sa.Float(), nullable=False),
        sa.Column("ema_25", sa.Float(), nullable=False),
        sa.Column("ema_99", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol", "cross_time", "signal_type",
            name="uq_signal_symbol_time_type",
        ),
    )
    op.create_index("ix_signal_symbol_created", "signals", ["symbol", "created_at"])
    op.create_index("ix_signals_symbol", "signals", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_signals_symbol", table_name="signals")
    op.drop_index("ix_signal_symbol_created", table_name="signals")
    op.drop_table("signals")

    op.drop_index("ix_candles_symbol", table_name="candles")
    op.drop_index("ix_candle_symbol_open_time", table_name="candles")
    op.drop_table("candles")
