# RGO OS — SQLite → PostgreSQL Portability Audit

Static audit of the entire SQL surface for constructs that behave differently on
PostgreSQL than on the SQLite dev/test database. Performed because the integrated
browser→PostgreSQL runtime proof cannot run in the current environment (no
Docker/PG host); this closes the *class* of bug that would otherwise first appear
on the Postgres deploy. Enforced permanently by `test_pg_portability.py` (6 tests).

## Findings

| Risk | Result |
|---|---|
| SQLite date funcs (`strftime`, `julianday`, `date()`, `datetime()`) | **None used** |
| `INSERT OR REPLACE` / `INSERT OR IGNORE` | **None** — the two upserts use portable `ON CONFLICT(col) DO UPDATE` |
| `GROUP_CONCAT`, `IFNULL`, `AUTOINCREMENT` | **None used** |
| `LIKE` with `%` wildcards | **None** |
| Literal `%` in any SQL (psycopg2 param collision) | **None** — every `%` in source is Python string-formatting in `pdfgen.py`/logging, never in SQL |
| Timestamp columns typed `TIMESTAMP` (would break `[:10]` string slicing) | **None** — all `*_at` columns are `TEXT`, so ISO-string ops stay valid on Postgres |

## `RETURNING id` completeness (the adapter's core mechanism)

`dbconn.PgConnection.execute` appends `RETURNING id` to INSERTs so `cur.lastrowid`
keeps working on PostgreSQL — but only for tables that actually have an integer
`id` primary key. The exclusion set `dbconn.NO_ID_TABLES` was verified against the
real schema:

- **Exactly three** tables have no integer `id` PK: `sessions` (token PK),
  `system_config` (key PK), `schema_version` (no PK). **All three are in
  `NO_ID_TABLES`.**
- **All ~37 other** INSERT-target tables declare `id INTEGER PRIMARY KEY`
  (→ `SERIAL PRIMARY KEY` after `pgcompat.to_postgres_ddl`), so `RETURNING id`
  is correct for each.
- `test_pg_portability.py` re-derives the id-less set from the DDL on every run and
  asserts it equals `NO_ID_TABLES`, so adding an id-less table without registering
  it fails CI **before** it can break a Postgres deploy.

## `ON CONFLICT` constraint backing

PostgreSQL (unlike SQLite) rejects `ON CONFLICT(col)` unless `col` has a
UNIQUE/PRIMARY KEY constraint. Both upserts are backed:
- `notification_templates.code` → `TEXT UNIQUE NOT NULL`
- `system_config.key` → `TEXT PRIMARY KEY`

The auto-appended `RETURNING id` lands legally after the `ON CONFLICT … DO UPDATE`
clause in both cases.

## Scope note

This is a **static** guarantee (dialect/shape correctness). It is **not** a
substitute for the live PostgreSQL runtime proof — concurrency, transaction
isolation, connection-pool behavior, and real migration execution must still be
observed on a running PostgreSQL instance per `DEPLOYMENT_VALIDATION.md`.
