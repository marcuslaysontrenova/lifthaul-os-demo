"""The PostgreSQL recovery playbook must never reveal database credentials."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("backup_restore.py")


class SafePostgresRecoveryPlaybook(unittest.TestCase):
    def test_postgres_runbook_does_not_print_secret_or_open_connections(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:PRIVATE_CANARY_SECRET@prod.example/db"}, clear=False):
            namespace = runpy.run_path(str(SCRIPT), run_name="recovery_contract")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(namespace["postgres_playbook"](), 0)
        body = output.getvalue()
        self.assertNotIn("PRIVATE_CANARY_SECRET", body)
        self.assertNotIn("prod.example", body)
        self.assertIn("NO BACKUP OR RESTORE EXECUTED", body)
        self.assertIn('service=lifthaul_restore', body)
        self.assertIn("STOP", body)

    def test_source_and_restore_services_are_distinct(self):
        namespace = runpy.run_path(str(SCRIPT), run_name="recovery_contract")
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            namespace["postgres_playbook"]()
        text = stream.getvalue()
        self.assertIn("service=lifthaul_source", text)
        self.assertIn("service=lifthaul_restore", text)
        self.assertNotIn("psql \"$DATABASE_URL\"", text)


if __name__ == "__main__":
    unittest.main()
