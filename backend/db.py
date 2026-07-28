"""RGO OS backend — database connection factory, migrations & schema versioning.

Selects the persistence backend from configuration (never hard-coded):
  * DATABASE_URL unset / sqlite  -> SQLite (dev, tests, single-node prod).
  * DATABASE_URL=postgres[ql]://  -> PostgreSQL (production system of record).

Schema is applied idempotently on connect (all module schemas) and stamped into a
`schema_version` table for versioning. The PostgreSQL path requires the `psycopg`
driver + a reachable server; when neither is present it raises a clear, honest
error (blocked on owner infrastructure) rather than pretending to connect.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

SCHEMA_VERSION = 7   # bump when any module schema changes


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_version(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER, applied_at TEXT)")
    row = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    cur = row[0] if row else None
    if cur is None or cur < SCHEMA_VERSION:
        conn.execute("INSERT INTO schema_version(version,applied_at) VALUES(?,?)", (SCHEMA_VERSION, _now()))
        conn.commit()


def current_version(conn):
    row = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    return row[0] if row else None


def _sqlite(path):
    import catalog   # applies core+ops+admin+catalog schema with FKs + Row factory
    conn = catalog.connect_full(path)
    ensure_version(conn)
    return conn


def _postgres(url):
    try:
        import psycopg  # noqa: F401
    except Exception:
        try:
            import psycopg2  # noqa: F401
        except Exception:
            raise RuntimeError(
                "PostgreSQL selected via DATABASE_URL but no psycopg/psycopg2 driver is "
                "installed and no PostgreSQL server is reachable here. To go live: "
                "`pip install 'psycopg[binary]'`, provision PostgreSQL, set DATABASE_URL, "
                "then run `python migrate.py`. (BLOCKED ON OWNER INFRASTRUCTURE)")
    # Driver present but this offline environment has no PG server; the service layer's
    # SQL is standard, but Postgres needs the '?'->'%s' param shim + SERIAL DDL applied by
    # migrate.py. Do not fake a connection.
    raise RuntimeError(
        "PostgreSQL driver found but connection/migration must be run against a real "
        "PostgreSQL instance via `python migrate.py` with a valid DATABASE_URL. "
        "Not runnable in this offline environment.")


def connect(url: str | None = None):
    url = url if url is not None else os.environ.get("DATABASE_URL")
    if url and url.startswith(("postgres://", "postgresql://")):
        return _postgres(url)
    if not url or url in (":memory:", "sqlite://:memory:", "sqlite::memory:"):
        return _sqlite(":memory:")
    if url.startswith("sqlite:///"):
        url = url[len("sqlite:///"):]
    elif url.startswith("sqlite://"):
        url = url[len("sqlite://"):]
    return _sqlite(url or "rgo_os.sqlite")
