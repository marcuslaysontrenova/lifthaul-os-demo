"""RGO OS — PostgreSQL compatibility helpers.

Provides the deterministic, testable building blocks for the Postgres path:
  * translate_params(sql): SQLite '?' placeholders -> psycopg '%s';
  * to_postgres_ddl(schema): SQLite DDL -> PostgreSQL DDL (SERIAL, DOUBLE PRECISION);
  * full_postgres_ddl(): the whole schema as PostgreSQL DDL.

HONEST SCOPE — the remaining Postgres-specific refactor (tracked, not yet done):
  1. `cur.lastrowid` has no PostgreSQL equivalent -> append `RETURNING id` and read it
     (the service layer uses lastrowid in ~20 inserts).
  2. `executescript` is SQLite-only -> execute statements individually (a splitter is
     provided by full_postgres_ddl()).
  3. `INSERT OR REPLACE` -> `INSERT ... ON CONFLICT (...) DO UPDATE`.
  4. `conn.backup` is SQLite-only -> use pg_dump/managed snapshots.
These are mechanical and localized; until a live PostgreSQL instance is available they
cannot be runtime-verified, so this module ships the verified pieces and documents the rest.
"""
from __future__ import annotations

import re


def translate_params(sql: str) -> str:
    # Our parameterized SQL contains no literal '?' characters, so a direct swap is safe.
    return sql.replace("?", "%s")


def to_postgres_ddl(schema: str) -> str:
    s = schema
    s = re.sub(r"INTEGER\s+PRIMARY\s+KEY", "SERIAL PRIMARY KEY", s)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    # SQLite PRAGMA/AUTOINCREMENT are not used in our schema; TEXT/BIGINT/FK are portable.
    return s


def full_postgres_ddl() -> str:
    import core, ops, admin, catalog, admin_platform, org, backfill, config_registry
    import masterdata, crm_admin, workflow, wfgov, forms
    import settings as sysconfig
    import integrations
    import reporting
    import ai_admin
    import saas
    import marketplace
    parts = [core.SCHEMA, ops.OPS_SCHEMA, admin.ADMIN_SCHEMA, catalog.CATALOG_SCHEMA,
             admin_platform.SCHEMA, org.SCHEMA, backfill.SCHEMA, config_registry.SCHEMA,
             masterdata.SCHEMA, crm_admin.SCHEMA,   # Phase 3 (crm_admin index references customers -> after core)
             workflow.SCHEMA, wfgov.SCHEMA,         # Phase 4 workflow engine + governance
             forms.SCHEMA,                          # Phase 5 form & custom-field engine
             sysconfig.SCHEMA,                      # Phase 6 platform & system settings
             integrations.SCHEMA,                   # Phase 7 integration administration + Wise
             reporting.SCHEMA,                      # Phase 8 reporting & dashboard administration
             ai_admin.SCHEMA,                       # Phase 9 AI administration
             saas.SCHEMA,                           # Phase 10 SaaS commercial layer
             marketplace.SCHEMA]                    # Marketplace foundation: taxonomy + eligibility + lanes
    return "\n".join(to_postgres_ddl(p) for p in parts)


def split_statements(ddl: str):
    return [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("PRAGMA")]
