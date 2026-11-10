"""api_keys 增加手动启停开关与到期时间

disabled 与吊销终态互补：可反复切换；expires_at 为 NULL 表示永不过期，
到期后鉴权拒绝，改回 NULL 或未来时间即恢复使用。

Revision ID: b7e2a9c15f13
Revises: a3f8c1d94e02
Create Date: 2026-11-10 15:05:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b7e2a9c15f13"
down_revision = "a3f8c1d94e02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(
            sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("expires_at")
        batch.drop_column("disabled")
