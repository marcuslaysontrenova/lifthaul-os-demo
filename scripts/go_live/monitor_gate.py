"""Evaluate production monitor steps independently and fail closed."""
import json
import os
from pathlib import Path


def evaluate(env):
    ownership = env.get("OWNERSHIP_OUTCOME") == "success"
    surface = env.get("SURFACE_OUTCOME") == "success"
    return {
        "ownership_passed": ownership,
        "surface_passed": surface,
        "monitor_passed": ownership and surface,
    }


def main():
    report = evaluate(os.environ)
    path = Path(os.environ.get("MONITOR_EVIDENCE_PATH", "evidence/monitor-gate.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Production monitor: ownership=" + ("PASS" if report["ownership_passed"] else "FAIL")
          + " surface=" + ("PASS" if report["surface_passed"] else "FAIL"))
    return 0 if report["monitor_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
