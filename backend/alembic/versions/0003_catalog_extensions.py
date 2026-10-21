"""catalogue : identifiants chiffrés, skills et hooks — RLS sur les nouvelles tables

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from harness.db.rls import enable_rls

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False, index=True),
    ]


def upgrade() -> None:
    op.create_table(
        "credentials",
        *_base(),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
    )
    op.add_column("tools", sa.Column("credential_id", sa.Uuid(), sa.ForeignKey("credentials.id"), nullable=True))
    op.add_column("tools", sa.Column("skill_name", sa.String(), nullable=True))
    op.add_column("tools", sa.Column("skill_description", sa.Text(), nullable=True))
    op.add_column("tools", sa.Column("files", postgresql.JSONB(), nullable=True))
    op.create_table(
        "hook_definitions",
        *_base(),
        sa.Column("tool_id", sa.Uuid(), sa.ForeignKey("tools.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("hook_type", sa.String(20), nullable=False),
        sa.Column("condition", postgresql.JSONB(), nullable=True),
        sa.Column("decision", sa.String(10), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("signing_secret_id", sa.Uuid(), sa.ForeignKey("credentials.id"), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )
    enable_rls("credentials")
    enable_rls("hook_definitions")


def downgrade() -> None:
    op.drop_table("hook_definitions")
    for column in ("files", "skill_description", "skill_name", "credential_id"):
        op.drop_column("tools", column)
    op.drop_table("credentials")
