"""RGO OS — PostgreSQL connection adapter.

Wraps a psycopg2 connection so the existing service code (written for SQLite:
`?` params, `cur.lastrowid`, `conn.execute`, `with conn:`, `sqlite_master` table
checks) runs UNCHANGED on PostgreSQL. This is the "RETURNING refactor" without
touching 30+ call sites:

  * params: `?`  -> `%s`;
  * `sqlite_master` existence checks -> `pg_tables`;
  * INSERTs into id-PK tables get `RETURNING id` appended so `cur.lastrowid` works;
  * `executescript` -> per-statement execution;
  * `with conn:` -> commit/rollback transaction;
  * rows are psycopg2 DictRow (support both `row["col"]` and `row[0]`), matching
    sqlite3.Row semantics the code relies on.
"""
from __future__ import annotations

import re

# Tables WITHOUT an integer `id` primary key (so we must NOT append RETURNING id).
NO_ID_TABLES = {"sessions", "system_config", "schema_version", "config_definitions",
                "setting_definitions", "modules",   # Phase 6: key/code-keyed, id-less
                "ai_tools",                          # Phase 9: code-keyed, id-less
                "usage_meters"}                      # Phase 10: code-keyed, id-less

_SQLITE_MASTER = re.compile(
    r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type='table'\s+AND\s+name='(\w+)'",
    re.IGNORECASE)
_INSERT_TABLE = re.compile(r"INSERT\s+INTO\s+(\w+)", re.IGNORECASE)


def pg_sql(sql: str) -> str:
    sql = _SQLITE_MASTER.sub(r"SELECT tablename AS name FROM pg_tables WHERE tablename='\1'", sql)
    return sql.replace("?", "%s")


def _returning_target(sql: str):
    m = _INSERT_TABLE.search(sql.lstrip())
    if not m:
        return None
    if "RETURNING" in sql.upper():
        return None
    table = m.group(1).lower()
    return None if table in NO_ID_TABLES else table


class _Cur:
    def __init__(self, cur, lastrowid=None):
        self._c = cur
        self.lastrowid = lastrowid

    @property
    def rowcount(self):
        # proxy the real psycopg2 cursor rowcount so INSERT ... ON CONFLICT DO NOTHING / UPDATE / DELETE
        # callers see the true affected-row count (was silently 0 before, breaking idempotency checks).
        return self._c.rowcount

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c)


class PgConnection:
    """DB-API-ish facade matching how the service layer uses sqlite3.Connection."""

    def __init__(self, raw):
        self._raw = raw
        import psycopg2.extras
        self._factory = psycopg2.extras.DictCursor

    def execute(self, sql, params=()):
        cur = self._raw.cursor(cursor_factory=self._factory)
        q = pg_sql(sql)
        if _returning_target(sql):
            q = q.rstrip().rstrip(";") + " RETURNING id"
            cur.execute(q, params)
            row = cur.fetchone()
            return _Cur(cur, row[0] if row else None)
        cur.execute(q, params)
        return _Cur(cur)

    def executescript(self, script):
        cur = self._raw.cursor()
        for stmt in script.split(";"):
            s = stmt.strip()
            if s and not s.upper().startswith("PRAGMA"):
                cur.execute(pg_sql(s))
        self._raw.commit()

    def cursor(self, *a, **k):
        return self._raw.cursor(cursor_factory=self._factory)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        return False


def apply_schema(conn):
    """Create the full schema on a PostgreSQL connection (DDL dialect-translated)."""
    import pgcompat
    cur = conn._raw.cursor()
    for stmt in pgcompat.split_statements(pgcompat.full_postgres_ddl()):
        cur.execute(stmt)
    conn.commit()
