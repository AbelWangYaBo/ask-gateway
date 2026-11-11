"""pricing_rules 加 effective_to / priority：生效周期与权重决胜

Revision ID: a9c3e2f17b41
Revises: c4d1f8a27e90
Create Date: 2026-11-11 11:08:10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a9c3e2f17b41"
down_revision = "c4d1f8a27e90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pricing_rules",
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pricing_rules",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("pricing_rules", "priority")
    op.drop_column("pricing_rules", "effective_to")
