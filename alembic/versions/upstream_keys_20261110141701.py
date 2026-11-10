"""upstream_keys 改造：密文/指纹/preview 三列取代 env_var_name 引用

上游 Key 从「环境变量引用」改为「AES-GCM 密文入库 + 指纹比对」，
支持管理端动态增删、池即时重配置。表此前无数据，batch 重建零风险。

Revision ID: a3f8c1d94e02
Revises: 5d13f32d7aa8
Create Date: 2026-11-10 14:17:01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a3f8c1d94e02"
down_revision = "5d13f32d7aa8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("upstream_keys") as batch:
        batch.drop_column("env_var_name")
        batch.add_column(sa.Column("key_ciphertext", sa.Text(), nullable=False))
        batch.add_column(
            sa.Column("key_fingerprint", sa.String(length=16), nullable=False)
        )
        batch.add_column(sa.Column("key_preview", sa.String(length=32), nullable=False))
    op.create_index(
        op.f("ix_upstream_keys_key_fingerprint"),
        "upstream_keys",
        ["key_fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_upstream_keys_key_fingerprint"), table_name="upstream_keys")
    with op.batch_alter_table("upstream_keys") as batch:
        batch.drop_column("key_preview")
        batch.drop_column("key_fingerprint")
        batch.drop_column("key_ciphertext")
        batch.add_column(
            sa.Column("env_var_name", sa.String(length=64), nullable=False)
        )
    op.create_unique_constraint(
        "uq_upstream_keys_env_var_name", "upstream_keys", ["env_var_name"]
    )
