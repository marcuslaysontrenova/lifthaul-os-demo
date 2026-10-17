#!/usr/bin/env python3
"""Capture read-only evidence for a controlled production rollback/redeploy phase."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def fetch(url: str) -> tuple[dict, float]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            if response.status != 200:
                raise SystemExit(f"{url} returned HTTP {response.status}")
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"unable to verify {url}: {exc}") from exc
    return data, round((time.monotonic() - started) * 1000, 2)


def main() -> int:
    base = required("BASE_URL").rstrip("/")
    expected_sha = required("EXPECTED_SHA").lower()
    expected_deployment = required("EXPECTED_DEPLOYMENT_ID")
    phase = required("EVIDENCE_PHASE")
    if phase not in {"rollback", "forward_restore"}:
        raise SystemExit("EVIDENCE_PHASE must be rollback or forward_restore")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise SystemExit("EXPECTED_SHA must be a full 40-character commit SHA")

    health, health_ms = fetch(base + "/healthz")
    ready, ready_ms = fetch(base + "/readyz")
    checks = {
        "health_ok": health.get("status") == "ok",
        "ready": ready.get("status") == "ready",
        "release_sha": str(ready.get("release_sha", "")).lower() == expected_sha,
        "deployment_id": str(ready.get("deployment_id", "")) == expected_deployment,
        "schema_stamped": ready.get("schema_version") is not None,
    }
    if not all(checks.values()):
        raise SystemExit(f"rollback verification failed: checks={checks}, ready={ready}")
    evidence = {
        "result": "PASS",
        "phase": phase,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "release_sha": expected_sha,
        "deployment_id": expected_deployment,
        "schema_version": ready["schema_version"],
        "health_latency_ms": health_ms,
        "ready_latency_ms": ready_ms,
        "checks": checks,
    }
    path = Path(required("EVIDENCE_PATH"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
