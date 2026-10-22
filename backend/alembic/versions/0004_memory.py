"""mémoire : entrées, révisions, propositions de mémoire de groupe — RLS

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401

from harness.db.rls import enable_rls

revision = "0004"
down_revision = "0003"
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
        "memory_entries",
        *_base(),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_type", sa.String(10), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_memory_entries_agent_path", "memory_entries", ["group_id", "agent_id", "path"], unique=True,
        postgresql_where=sa.text("scope = 'agent'"),
    )
    op.create_index(
        "uq_memory_entries_group_path", "memory_entries", ["group_id", "path"], unique=True,
        postgresql_where=sa.text("scope = 'group'"),
    )
    op.create_table(
        "memory_revisions",
        *_base(),
        sa.Column(
            "memory_entry_id", sa.Uuid(), sa.ForeignKey("memory_entries.id", ondelete="CASCADE"), nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_content", sa.Text(), nullable=True),
        sa.Column("new_content", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_type", sa.String(10), nullable=False),
    )
    op.create_table(
        "group_memory_proposals",
        *_base(),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposed_path", sa.String(512), nullable=False),
        sa.Column("proposed_content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending", index=True),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("persons.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    for table in ("memory_entries", "memory_revisions", "group_memory_proposals"):
        enable_rls(table)


def downgrade() -> None:
    for table in ("group_memory_proposals", "memory_revisions", "memory_entries"):
        op.drop_table(table)
