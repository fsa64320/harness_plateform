"""Row-Level Security : politiques d'isolation par groupe et rôle applicatif sans BYPASSRLS.

Utilisé par les migrations Alembic (FR-022, SC-009).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from alembic import op

GROUP_SETTING = "NULLIF(current_setting('app.group_id', true), '')::uuid"
PERSON_SETTING = "NULLIF(current_setting('app.person_id', true), '')::uuid"
PLATFORM_ON = "COALESCE(current_setting('app.platform_admin', true), '') = 'on'"


def enable_rls(
    table: str,
    *,
    group_column: str = "group_id",
    also_using: str | None = None,
    select_also: str | None = None,
) -> None:
    """Active et FORCE la RLS sur `table` : une ligne n'est visible et modifiable que si son
    `group_id` égale `app.group_id`. `also_using` élargit la politique (lignes sans groupe, p. ex.),
    `select_also` ajoute une politique en lecture seule (appartenances d'une personne)."""
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    expr = f"{group_column} = {GROUP_SETTING}"
    if also_using:
        expr = f"({expr}) OR ({also_using})"
    op.execute(f'CREATE POLICY group_isolation ON "{table}" USING ({expr}) WITH CHECK ({expr})')
    if select_also:
        op.execute(f'CREATE POLICY select_also ON "{table}" FOR SELECT USING ({select_also})')


def ensure_app_role(database_url: str, owner_url: str) -> None:
    """Crée (ou met à jour) le rôle applicatif décrit par `database_url` : LOGIN, ni superuser ni
    BYPASSRLS, droits DML sur les tables présentes et futures du propriétaire des migrations.

    Si l'application se connecte avec le rôle propriétaire (développement mono-rôle), rien n'est
    fait ; le démarrage refusera alors un rôle capable de contourner la RLS."""
    app, owner = urlsplit(database_url), urlsplit(owner_url)
    if not app.username or app.username == owner.username:
        return
    password = app.password or ""
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {_lit(app.username)}) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L',
                           {_lit(app.username)}, {_lit(password)});
          ELSE
            EXECUTE format('ALTER ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L',
                           {_lit(app.username)}, {_lit(password)});
          END IF;
        END $$;
        """
    )
    role = _ident(app.username)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
