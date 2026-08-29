"""RGO OS — PostgreSQL adapter unit tests (no live server; fake DB-API cursor)."""
import unittest
import dbconn


class FakeCur:
    def __init__(self, log):
        self.log = log
        self._ret = None

    def execute(self, sql, params=()):
        self.log.append((sql, params))
        self._ret = (42,) if "RETURNING" in sql.upper() else None

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return []

    def __iter__(self):
        return iter([])


class FakeRaw:
    def __init__(self):
        self.log = []
        self.committed = 0
        self.rolled_back = 0

    def cursor(self, *a, **k):
        return FakeCur(self.log)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class TestPgSql(unittest.TestCase):
    def test_param_translation(self):
        self.assertEqual(dbconn.pg_sql("WHERE a=? AND b=?"), "WHERE a=%s AND b=%s")

    def test_sqlite_master_translation(self):
        out = dbconn.pg_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='equipment'")
        self.assertIn("pg_tables", out)
        self.assertIn("tablename='equipment'", out)
        self.assertNotIn("sqlite_master", out)

    def test_table_info_pragma_preserves_sqlite_row_contract(self):
        out = dbconn.pg_sql("PRAGMA table_info(quotation_lines)")
        self.assertIn("information_schema.columns", out)
        self.assertIn("AS cid", out)
        self.assertIn("AS name", out)
        self.assertIn("table_name='quotation_lines'", out)
        self.assertIn("ORDER BY ordinal_position", out)
        self.assertNotIn("PRAGMA", out.upper())

    def test_incremental_create_table_uses_postgres_identity_and_numeric_types(self):
        out = dbconn.pg_sql("CREATE TABLE notify_policy(id INTEGER PRIMARY KEY, rate REAL)")
        self.assertIn("id SERIAL PRIMARY KEY", out)
        self.assertIn("rate DOUBLE PRECISION", out)
        self.assertNotIn("INTEGER PRIMARY KEY", out)

    def test_returning_target(self):
        self.assertEqual(dbconn._returning_target("INSERT INTO jobs(a) VALUES(?)"), "jobs")
        self.assertIsNone(dbconn._returning_target("INSERT INTO sessions(token) VALUES(?)"))     # no id PK
        self.assertIsNone(dbconn._returning_target("INSERT INTO system_config(key) VALUES(?)"))  # no id PK
        self.assertIsNone(dbconn._returning_target("INSERT INTO jobs(a) VALUES(?) RETURNING id"))
        self.assertIsNone(dbconn._returning_target("UPDATE jobs SET a=?"))


class TestPgConnection(unittest.TestCase):
    def setUp(self):
        # avoid importing psycopg2.extras in __init__ during unit test
        self.conn = dbconn.PgConnection.__new__(dbconn.PgConnection)
        self.raw = FakeRaw()
        self.conn._raw = self.raw
        self.conn._factory = None

    def test_insert_appends_returning_and_captures_lastrowid(self):
        cur = self.conn.execute("INSERT INTO jobs(a,b) VALUES(?,?)", (1, 2))
        sql, params = self.raw.log[-1]
        self.assertIn("RETURNING id", sql)
        self.assertIn("%s", sql)
        self.assertEqual(cur.lastrowid, 42)

    def test_no_id_table_no_returning(self):
        cur = self.conn.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", ("t", 1, "now"))
        sql, _ = self.raw.log[-1]
        self.assertNotIn("RETURNING", sql.upper())
        self.assertIsNone(cur.lastrowid)

    def test_select_not_mutated_with_returning(self):
        self.conn.execute("SELECT * FROM jobs WHERE id=?", (1,))
        sql, _ = self.raw.log[-1]
        self.assertNotIn("RETURNING", sql.upper())
        self.assertIn("%s", sql)

    def test_executescript_splits(self):
        self.conn.executescript("CREATE TABLE a(id INTEGER PRIMARY KEY); PRAGMA x; CREATE TABLE b(id INTEGER);")
        stmts = [s for s, _ in self.raw.log]
        self.assertEqual(len([s for s in stmts if s.upper().startswith("CREATE")]), 2)
        self.assertFalse(any("PRAGMA" in s.upper() for s in stmts))   # PRAGMA dropped
        self.assertIn("SERIAL PRIMARY KEY", stmts[0])

    def test_context_manager_commits_and_rolls_back(self):
        with self.conn:
            pass
        self.assertEqual(self.raw.committed, 1)
        try:
            with self.conn:
                raise ValueError("boom")
        except ValueError:
            pass
        self.assertEqual(self.raw.rolled_back, 1)


class TestPostgresDDL(unittest.TestCase):
    def test_full_ddl_is_postgres_dialect(self):
        import pgcompat
        ddl = pgcompat.full_postgres_ddl()
        self.assertIn("SERIAL PRIMARY KEY", ddl)
        self.assertIn("DOUBLE PRECISION", ddl)
        self.assertNotIn(" REAL", ddl)                 # REAL fully translated


if __name__ == "__main__":
    unittest.main(verbosity=2)
