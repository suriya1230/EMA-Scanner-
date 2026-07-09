"""Add interval column to signals table — supports 1h, 2h, 4h, 6h

Revision ID: 003
Revises: 002
Create Date: 2026-07-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add interval column — default "1h" so all existing rows get a value
    op.add_column(
        "signals",
        sa.Column("interval", sa.String(5), nullable=False, server_default="1h"),
    )

    # 2. Drop old unique constraint (symbol, cross_time, signal_type) — no interval
    op.drop_constraint("uq_signal_symbol_time_type", "signals", type_="unique")

    # 3. Create new unique constraint including interval
    op.create_unique_constraint(
        "uq_signal_symbol_interval_time_type",
        "signals",
        ["symbol", "interval", "cross_time", "signal_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_signal_symbol_interval_time_type", "signals", type_="unique")
    op.create_unique_constraint(
        "uq_signal_symbol_time_type", "signals", ["symbol", "cross_time", "signal_type"]
    )
    op.drop_column("signals", "interval")