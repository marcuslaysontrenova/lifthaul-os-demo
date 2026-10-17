"""Run the controlled persona UAT suite and write machine-readable evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "test")


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.perf_counter()
        super().startTest(test)

    def _record(self, test, status, detail=None):
        elapsed = round(time.perf_counter() - self.started.get(test.id(), time.perf_counter()), 3)
        row = {"test": test.id(), "status": status, "duration_seconds": elapsed}
        if detail:
            row["detail"] = detail
        self.records.append(row)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "FAIL", self._exc_info_to_string(err, test)[-2000:])

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "ERROR", self._exc_info_to_string(err, test)[-2000:])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "SKIP", reason)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "audit" / "mvp-persona-uat.json"))
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromName("test_mvp_persona_uat")
    runner = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult)
    result = runner.run(suite)
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "lifthaul.mvp.persona-uat.v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "scope": "isolated synthetic acceptance; no production writes, messages, dispatches or funds",
        "production_gate_closed": False,
        "production_gate_note": (
            "A pass proves deployable workflow behavior only. Gate 5 closes after dedicated production "
            "persona sign-ins are witnessed and recorded on the exact deployed commit."
        ),
        "tests_run": result.testsRun,
        "passed": sum(row["status"] == "PASS" for row in result.records),
        "failed": len(result.failures) + len(result.errors),
        "skipped": len(result.skipped),
        "results": result.records,
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Persona UAT evidence: {output}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
