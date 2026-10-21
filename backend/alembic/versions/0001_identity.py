"""identité : personnes, groupes, appartenances, journal d'administration (+ rôle applicatif, RLS)

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from harness.config import get_settings
from harness.db.rls import GROUP_SETTING, PERSON_SETTING, PLATFORM_ON, enable_rls, ensure_app_role

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    settings = get_settings()
    ensure_app_role(settings.database_url, settings.migration_database_url or settings.database_url)

    op.create_table(
        "persons",
        *_base_columns(),
        sa.Column("oidc_subject", sa.String(), nullable=True, unique=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False, server_default=""),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "groups",
        *_base_columns(),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("trace_retention_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("approval_timeout_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("proposal_expiry_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("cost_per_1k_tokens", sa.Numeric(12, 6), nullable=False, server_default="0"),
    )
    op.create_table(
        "memberships",
        *_base_columns(),
        sa.Column("group_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id"), nullable=False, index=True),
        sa.Column(
            "roles", postgresql.JSONB(), nullable=False, server_default=sa.text("""'["member"]'::jsonb""")
        ),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("max_tokens_per_day", sa.Integer(), nullable=False, server_default="1000000"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.UniqueConstraint("group_id", "person_id"),
    )
    op.create_table(
        "admin_audit_entries",
        *_base_columns(),
        sa.Column("group_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("persons.id"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Une personne lit ses propres appartenances pour savoir à quels groupes elle accède.
    enable_rls("memberships", select_also=f"person_id = {PERSON_SETTING}")
    # Les actions de plateforme n'ont pas de groupe : visibles seulement en contexte plateforme.
    enable_rls("admin_audit_entries", also_using=f"group_id IS NULL AND {PLATFORM_ON}")

    # `groups` : visible pour ses membres et pour l'administration de plateforme ; seule la
    # plateforme crée ou modifie un groupe. Aucun contenu de groupe n'est ouvert par ce contexte.
    op.execute('ALTER TABLE "groups" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "groups" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""CREATE POLICY group_visibility ON "groups" FOR SELECT USING (
              id = {GROUP_SETTING}
              OR {PLATFORM_ON}
              OR id IN (SELECT group_id FROM memberships WHERE person_id = {PERSON_SETTING}))"""
    )
    op.execute(
        f'CREATE POLICY group_platform_write ON "groups" FOR ALL USING ({PLATFORM_ON}) WITH CHECK ({PLATFORM_ON})'
    )


def downgrade() -> None:
    for table in ("admin_audit_entries", "memberships", "groups", "persons"):
        op.drop_table(table)
