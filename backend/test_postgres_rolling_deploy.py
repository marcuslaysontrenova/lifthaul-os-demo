"""Regression contract for transaction cleanup during Railway rolling deploys."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent

class TestPostgresRollingDeploySafety(unittest.TestCase):
    def test_successful_requests_and_readiness_end_transactions(self):
        text = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("result = _do_request()\n                        # All successful requests", text)
        self.assertIn("_conn.commit()\n                    except Exception:", text)
        self.assertIn("_conn_single.rollback()\n                else:", text)

    def test_schema_lock_waits_are_bounded(self):
        text = (ROOT / "db.py").read_text(encoding="utf-8")
        self.assertIn("psycopg2.connect(url, connect_timeout=10)", text)
        self.assertIn('SET lock_timeout = \'12s\'', text)
        self.assertIn('SET statement_timeout = \'60s\'', text)
        self.assertIn("dbconn.apply_schema(conn)", text)
        self.assertIn("conn.rollback()\n        conn.close()\n        raise", text)

if __name__ == "__main__":
    unittest.main()
