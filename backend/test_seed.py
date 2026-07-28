"""RGO OS — demo seed + Postgres-compat tests."""
import unittest
import db
import seed
import pgcompat


class TestSeed(unittest.TestCase):
    def test_seed_runs_and_is_idempotent(self):
        conn = db.connect()
        r = seed.seed(conn)
        self.assertTrue(r["seeded"])
        self.assertTrue(r["job"].startswith("JO-"))
        # demo users + a customer login exist
        self.assertGreaterEqual(conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"], 6)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"], 1)
        # re-seed is a no-op (already has data)
        r2 = seed.seed(conn)
        self.assertFalse(r2["seeded"])
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"], 1)


class TestPgCompat(unittest.TestCase):
    def test_translate_params(self):
        self.assertEqual(pgcompat.translate_params("SELECT * FROM t WHERE a=? AND b=?"),
                         "SELECT * FROM t WHERE a=%s AND b=%s")

    def test_ddl_dialect(self):
        pg = pgcompat.to_postgres_ddl("CREATE TABLE t(id INTEGER PRIMARY KEY, amt REAL, name TEXT);")
        self.assertIn("SERIAL PRIMARY KEY", pg)
        self.assertIn("DOUBLE PRECISION", pg)

    def test_full_ddl_and_split(self):
        ddl = pgcompat.full_postgres_ddl()
        self.assertIn("SERIAL PRIMARY KEY", ddl)
        stmts = pgcompat.split_statements(ddl)
        self.assertTrue(all("PRAGMA" not in s for s in stmts))
        self.assertTrue(any(s.startswith("CREATE TABLE") for s in stmts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
