"""RGO OS — static PostgreSQL-portability guard.

The full browser->PostgreSQL runtime proof needs a Docker/PG host and has not run
in this environment. This suite catches the *class* of bug that only surfaces on
first contact with PostgreSQL, statically, so it fails at CI time instead of at
deploy time:

  1. dbconn.NO_ID_TABLES must be EXACTLY the set of tables with no integer `id`
     primary key. If it under-lists, the adapter appends `RETURNING id` to an
     INSERT into an id-less table and PostgreSQL errors. If it over-lists, an
     id-PK insert silently loses `lastrowid`.
  2. Every `ON CONFLICT(col)` upsert target must have a UNIQUE/PRIMARY KEY
     constraint on `col`, or PostgreSQL raises "no unique or exclusion constraint
     matching the ON CONFLICT specification" (SQLite is laxer and would pass).
  3. No raw SQL may contain a literal `%` (psycopg2 treats `%` as a parameter
     marker; `pg_sql` turns `?`->`%s`, so a stray `%` would corrupt the query).
  4. pg_sql leaves no `?` placeholder behind.

These are deterministic and require no live database.
"""
import io
import os
import re
import tokenize
import unittest

import core
import ops
import admin
import catalog
import admin_platform
import org
import backfill
import config_registry
import masterdata
import crm_admin
import workflow
import wfgov
import forms
import settings as sysconfig
import integrations
import reporting
import ai_admin
import dbconn

# The canonical DDL surface (same parts pgcompat.full_postgres_ddl assembles),
# plus the versioning table created imperatively in db.py.
_SCHEMA_PARTS = [core.SCHEMA, ops.OPS_SCHEMA, admin.ADMIN_SCHEMA, catalog.CATALOG_SCHEMA,
                 admin_platform.SCHEMA, org.SCHEMA, backfill.SCHEMA, config_registry.SCHEMA,
                 masterdata.SCHEMA, crm_admin.SCHEMA, workflow.SCHEMA, wfgov.SCHEMA, forms.SCHEMA,
                 sysconfig.SCHEMA, integrations.SCHEMA, reporting.SCHEMA, ai_admin.SCHEMA,
                 "CREATE TABLE IF NOT EXISTS schema_version(version INTEGER, applied_at TEXT);"]
_ALL_DDL = "\n".join(_SCHEMA_PARTS)

_CREATE = re.compile(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\);",
                     re.IGNORECASE | re.DOTALL)


def _tables():
    """{table_name: column-body text} for every declared table."""
    return {m.group(1).lower(): m.group(2) for m in _CREATE.finditer(_ALL_DDL)}


def _has_integer_id_pk(body: str) -> bool:
    return re.search(r"\bid\s+INTEGER\s+PRIMARY\s+KEY", body, re.IGNORECASE) is not None


def _backend_dir():
    return os.path.dirname(os.path.abspath(__file__))


_SQL_START = re.compile(r"^\s*(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|WITH|CREATE\s+TABLE)\b",
                        re.IGNORECASE)


def _coalesced_strings(src: str):
    """Reconstruct implicitly-concatenated adjacent string literals via the tokenizer.

    Python joins `"a" "b"` (no operator between) into one string; a per-literal regex
    scan would split the notification_templates upsert (INSERT in one literal, ON
    CONFLICT in the next). Coalescing consecutive STRING tokens rebuilds the real
    statement; any OP (comma, plus, paren) between literals ends a run.
    """
    run = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            try:
                run.append(eval(tok.string, {"__builtins__": {}}, {}))   # literal only, no names
            except Exception:
                run.clear()
        elif tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
                          tokenize.INDENT, tokenize.DEDENT):
            continue
        else:
            if run:
                yield "".join(str(x) for x in run)
                run.clear()
    if run:
        yield "".join(str(x) for x in run)


def _sql_string_literals():
    """Yield (filename, statement) for every source string that STARTS with a SQL verb."""
    for fn in os.listdir(_backend_dir()):
        if not fn.endswith(".py") or fn.startswith("test_"):
            continue
        with open(os.path.join(_backend_dir(), fn), encoding="utf-8") as fh:
            src = fh.read()
        for s in _coalesced_strings(src):
            if _SQL_START.match(s):
                yield fn, s


class TestNoIdTablesExhaustive(unittest.TestCase):
    def test_no_id_tables_is_exactly_the_idless_set(self):
        tables = _tables()
        self.assertGreater(len(tables), 20, "schema parse failed")
        idless = {t for t, body in tables.items() if not _has_integer_id_pk(body)}
        self.assertEqual(
            idless, set(dbconn.NO_ID_TABLES),
            "dbconn.NO_ID_TABLES must equal the set of tables lacking an integer id PK. "
            f"schema-derived id-less tables={sorted(idless)}, "
            f"NO_ID_TABLES={sorted(dbconn.NO_ID_TABLES)}. A mismatch breaks RETURNING id on PostgreSQL.")

    def test_every_idpk_table_gets_returning(self):
        for t, body in _tables().items():
            if _has_integer_id_pk(body):
                self.assertEqual(dbconn._returning_target(f"INSERT INTO {t}(a) VALUES(?)"), t)

    def test_idless_tables_never_get_returning(self):
        for t in dbconn.NO_ID_TABLES:
            self.assertIsNone(dbconn._returning_target(f"INSERT INTO {t}(a) VALUES(?)"))


class TestOnConflictBackedByConstraint(unittest.TestCase):
    def test_conflict_targets_have_unique_constraint(self):
        tables = _tables()
        found = 0
        for fn, s in _sql_string_literals():
            for cm in re.finditer(r"ON CONFLICT\s*\(\s*(\w+)\s*\)", s, re.IGNORECASE):
                col = cm.group(1).lower()
                im = re.search(r"INSERT INTO\s+(\w+)", s, re.IGNORECASE)
                self.assertIsNotNone(im, f"{fn}: ON CONFLICT without INSERT INTO in same statement")
                table = im.group(1).lower()
                body = tables.get(table, "")
                # the column line must declare PRIMARY KEY or UNIQUE (col-level or table-level)
                col_unique = re.search(rf"\b{col}\b[^,]*\b(PRIMARY KEY|UNIQUE)\b", body, re.IGNORECASE)
                tbl_unique = re.search(rf"UNIQUE\s*\([^)]*\b{col}\b[^)]*\)", body, re.IGNORECASE)
                self.assertTrue(col_unique or tbl_unique,
                                f"{fn}: ON CONFLICT({col}) on {table} has no backing UNIQUE/PK constraint")
                found += 1
        self.assertGreaterEqual(found, 2, "expected to audit the known upserts")


class TestNoPsycopgPercentTrap(unittest.TestCase):
    def test_no_literal_percent_in_sql(self):
        for fn, s in _sql_string_literals():
            self.assertNotIn("%", s, f"{fn}: literal '%' in SQL would collide with psycopg2 params: {s!r}")

    def test_pg_sql_leaves_no_placeholder(self):
        for _, s in _sql_string_literals():
            self.assertNotIn("?", dbconn.pg_sql(s), f"pg_sql left a '?' in: {s!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
