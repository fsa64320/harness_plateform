"""catalogue (serveurs MCP), agents, exécutions, trace, approbations — RLS sur chaque table

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from harness.db.rls import enable_rls

revision = "0002"
down_revision = "0001"
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
        "tools",
        *_base(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("persons.id"), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_ok", sa.Boolean(), nullable=True),
        sa.UniqueConstraint("group_id", "kind", "name"),
    )
    op.create_table(
        "tool_operations",
        *_base(),
        sa.Column("tool_id", sa.Uuid(), sa.ForeignKey("tools.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("operation_name", sa.String(), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("classification", sa.String(20), nullable=False, server_default="unclassified"),
        sa.UniqueConstraint("tool_id", "operation_name"),
    )
    op.create_table(
        "agents",
        *_base(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("persons.id"), nullable=True),
        sa.Column("allowed_tool_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("agent_memory_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("group_memory_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("subagents_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("config_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "runs",
        *_base(),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("agent_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued", index=True),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("triggered_by_type", sa.String(20), nullable=False),
        sa.Column("triggered_by_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("launch_mode", sa.String(20), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("relaunched_from", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("active_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
    )
    op.create_table(
        "trace_events",
        *_base(),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "seq"),
    )
    op.create_index("ix_trace_events_group_run_seq", "trace_events", ["group_id", "run_id", "seq"])
    op.create_table(
        "approval_requests",
        *_base(),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("call_id", sa.String(), nullable=True),
        sa.Column("interrupt_id", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_operation", sa.String(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("no_eligible_approver", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("persons.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
    )
    for table in ("tools", "tool_operations", "agents", "runs", "trace_events", "approval_requests"):
        enable_rls(table)


def downgrade() -> None:
    for table in ("approval_requests", "trace_events", "runs", "agents", "tool_operations", "tools"):
        op.drop_table(table)
