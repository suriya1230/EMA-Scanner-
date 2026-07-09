"""Add market column to candles and signals — supports spot and futures

Revision ID: 004
Revises: 003
Create Date: 2026-07-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── candles ──────────────────────────────────────────────────────────────
    op.add_column(
        "candles",
        sa.Column("market", sa.String(10), nullable=False, server_default="futures"),
    )
    op.drop_constraint("uq_candle_symbol_interval_open_time", "candles", type_="unique")
    op.drop_index("ix_candle_symbol_interval_open_time", table_name="candles")
    op.create_unique_constraint(
        "uq_candle_symbol_market_interval_open_time",
        "candles",
        ["symbol", "market", "interval", "open_time"],
    )
    op.create_index(
        "ix_candle_symbol_market_interval_open_time",
        "candles",
        ["symbol", "market", "interval", "open_time"],
    )

    # ── signals ──────────────────────────────────────────────────────────────
    op.add_column(
        "signals",
        sa.Column("market", sa.String(10), nullable=False, server_default="futures"),
    )
    op.drop_constraint("uq_signal_symbol_interval_time_type", "signals", type_="unique")
    op.create_unique_constraint(
        "uq_signal_symbol_market_interval_time_type",
        "signals",
        ["symbol", "market", "interval", "cross_time", "signal_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_signal_symbol_market_interval_time_type", "signals", type_="unique")
    op.create_unique_constraint(
        "uq_signal_symbol_interval_time_type",
        "signals",
        ["symbol", "interval", "cross_time", "signal_type"],
    )
    op.drop_column("signals", "market")

    op.drop_index("ix_candle_symbol_market_interval_open_time", table_name="candles")
    op.drop_constraint("uq_candle_symbol_market_interval_open_time", "candles", type_="unique")
    op.create_unique_constraint(
        "uq_candle_symbol_interval_open_time",
        "candles",
        ["symbol", "interval", "open_time"],
    )
    op.create_index(
        "ix_candle_symbol_interval_open_time", "candles", ["symbol", "interval", "open_time"]
    )
    op.drop_column("candles", "market")
