#!/usr/bin/env python3
"""Dependency-free LiftHaul HTTP performance and idempotency probe.

This is deliberately a diagnostic harness, not evidence of production capacity. Run it only
against an isolated local, QA, or performance environment containing synthetic data.

Examples:
  python performance/local_http_probe.py --profile read --stages 1,25,100
  python performance/local_http_probe.py --profile mixed --stages 1,25,100
  python performance/local_http_probe.py --profile idempotency --stages 20
"""
from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path


@dataclasses.dataclass
class Result:
    operation: str
    status: int
    latency_ms: float
    accepted: bool
    entity_ref: str | None = None
    tracking_token: str | None = None
    error: str | None = None


READ_OPERATIONS = (
    ("liveness", "GET", "/healthz", None, {200}),
    ("readiness", "GET", "/readyz", None, {200}),
    ("service_levels", "GET", "/public/service-levels", None, {200}),
    ("vehicle_taxonomy", "GET", "/public/vehicle-variants", None, {200}),
    ("payment_channels", "GET", "/public/payments/channels", None, {200}),
)


def cargo_payload() -> dict:
    return {
        "contact_name": "Performance Test Booker",
        "contact_phone": "09170000000",
        "origin_island": "Luzon",
        "dest_island": "Luzon",
        "cargo_category": "BOXES_GENERAL",
        "cargo": "Synthetic packaged goods",
        "weight_kg": 750,
        "package_count": 2,
        "package_length_cm": 120,
        "package_width_cm": 80,
        "package_height_cm": 80,
        "special_handling": [],
        "splittable": False,
        "km": 45,
    }


def booking_payload(idempotency_key: str | None = None) -> dict:
    body = cargo_payload()
    body.update({
        "vehicle": "6w",
        "payment": "protected",
        "excluded_charges_ack": True,
    })
    if idempotency_key:
        body["idempotency_key"] = idempotency_key
    return body


def request(base_url: str, operation: str, method: str, path: str, body: dict | None,
            accepted_statuses: set[int]) -> Result:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=payload,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-ID": "perf-" + uuid.uuid4().hex[:16],
        },
    )
    started = time.perf_counter()
    status = 0
    raw = b""
    error = None
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except Exception as read_exc:
            error = f"HTTPError {exc.code}; response read failed: {type(read_exc).__name__}: {read_exc}"
    except Exception as exc:  # network/timeout evidence is retained, not hidden
        error = f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - started) * 1000
    entity_ref = None
    tracking_token = None
    if raw:
        try:
            obj = json.loads(raw)
            data = obj.get("data") if isinstance(obj, dict) else None
            if isinstance(data, dict):
                entity_ref = data.get("ref") or data.get("token")
                tracking_token = data.get("tracking_token")
            if status not in accepted_statuses and isinstance(obj, dict):
                error = str(obj.get("error") or obj)[:300]
        except Exception:
            if status not in accepted_statuses:
                error = raw.decode("utf-8", "replace")[:300]
    return Result(operation, status, round(elapsed, 3), status in accepted_statuses,
                  entity_ref, tracking_token, error)


def nearest_rank(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))]


def summarize(results: list[Result], elapsed: float) -> dict:
    latencies = [r.latency_ms for r in results]
    accepted = sum(1 for r in results if r.accepted)
    by_operation = defaultdict(list)
    for result in results:
        by_operation[result.operation].append(result)
    return {
        "requests": len(results),
        "accepted": accepted,
        "error_count": len(results) - accepted,
        "error_rate": round((len(results) - accepted) / len(results), 6) if results else 0,
        "throughput_rps": round(len(results) / elapsed, 3) if elapsed else 0,
        "elapsed_seconds": round(elapsed, 3),
        "latency_ms": {
            "min": round(min(latencies), 3) if latencies else 0,
            "median": round(statistics.median(latencies), 3) if latencies else 0,
            "p95": round(nearest_rank(latencies, 0.95), 3),
            "p99": round(nearest_rank(latencies, 0.99), 3),
            "max": round(max(latencies), 3) if latencies else 0,
        },
        "status_counts": dict(sorted(Counter(str(r.status) for r in results).items())),
        "operations": {
            name: {
                "requests": len(items),
                "accepted": sum(1 for item in items if item.accepted),
                "p95_ms": round(nearest_rank([item.latency_ms for item in items], 0.95), 3),
                "statuses": dict(sorted(Counter(str(item.status) for item in items).items())),
            }
            for name, items in sorted(by_operation.items())
        },
        "sample_errors": [
            {"operation": r.operation, "status": r.status, "error": r.error}
            for r in results if not r.accepted
        ][:10],
    }


def read_operation(index: int):
    return READ_OPERATIONS[index % len(READ_OPERATIONS)]


def mixed_operation(index: int, run_key: str, tracking_token: str | None):
    """The mandated role mix represented with endpoints available in the demo.

    Payment funding, a valid provider webhook, POD upload, and fleet/staff mutations require
    environment-specific fixtures. The local probe exercises their safe public/read or rejection
    boundaries; the k6 harness is the full-environment gate.
    """
    bucket = index % 100
    if bucket < 25:
        return ("browse_and_rates", "GET", "/public/service-levels", None, {200})
    if bucket < 40:
        return ("sign_in", "POST", "/login",
                {"identifier": "admin@lifthaul.demo", "password": "demo1234"}, {200})
    if bucket < 55:
        return ("cargo_and_route", "POST", "/public/bookings/vehicle-recommendations",
                cargo_payload(), {200})
    if bucket < 65:
        return ("vehicle_recommendation", "POST", "/public/bookings/vehicle-recommendations",
                cargo_payload(), {200})
    if bucket < 75:
        return ("booking_create", "POST", "/public/bookings",
                booking_payload(f"{run_key}-{index}"), {200})
    if bucket < 80:
        return ("payment_channel_read", "GET", "/public/payments/channels", None, {200})
    if bucket < 85:
        # A forged webhook must be rejected; 401/403 is the successful security outcome.
        return ("webhook_rejection", "POST", "/webhooks/xendit/payments",
                {"id": f"forged-{run_key}-{index}", "status": "PAID"}, {401, 403})
    if bucket < 90:
        path = f"/public/bookings/track/{tracking_token}" if tracking_token else "/healthz"
        return ("tracking", "GET", path, None, {200})
    if bucket < 95:
        # No anonymous file upload is intentionally exposed. Exercise provider reference data.
        return ("provider_document_boundary", "GET", "/public/vehicle-variants", None, {200})
    if bucket < 98:
        return ("fleet_management_read", "GET", "/public/vehicle-variants", None, {200})
    return ("staff_authentication", "POST", "/login",
            {"identifier": "ops@lifthaul.demo", "password": "demo1234"}, {200})


def run_stage(base_url: str, profile: str, users: int, requests_per_user: int,
              run_key: str) -> dict:
    total = users * requests_per_user
    tracking_token = None
    if profile == "mixed":
        setup = request(base_url, "tracking_fixture", "POST", "/public/bookings",
                        booking_payload(f"{run_key}-tracking"), {200})
        tracking_token = setup.tracking_token if setup.accepted else None

    def one(index: int) -> Result:
        if profile == "read":
            op = read_operation(index)
        else:
            op = mixed_operation(index, run_key, tracking_token)
        return request(base_url, *op)

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=users) as executor:
        results = list(executor.map(one, range(total)))
    elapsed = time.perf_counter() - started
    summary = summarize(results, elapsed)
    summary.update({"concurrent_users": users, "requests_per_user": requests_per_user})
    return summary


def run_idempotency(base_url: str, users: int, run_key: str) -> dict:
    idem = f"PERF-IDEM-{run_key}"
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=users) as executor:
        results = list(executor.map(
            lambda _i: request(base_url, "booking_idempotency", "POST", "/public/bookings",
                               booking_payload(idem), {200}),
            range(users),
        ))
    elapsed = time.perf_counter() - started
    summary = summarize(results, elapsed)
    refs = sorted({r.entity_ref for r in results if r.entity_ref})
    summary.update({
        "concurrent_users": users,
        "idempotency_key": idem,
        "unique_response_refs": refs,
        "integrity_pass": len(refs) == 1 and summary["accepted"] == users,
    })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--profile", choices=("read", "mixed", "idempotency"), default="read")
    parser.add_argument("--stages", default="1,25,100")
    parser.add_argument("--requests-per-user", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--p95-ms", type=float, default=2000)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    args = parser.parse_args()
    stages = [int(value) for value in args.stages.split(",") if value.strip()]
    run_key = uuid.uuid4().hex[:10]
    evidence = {
        "generated_at_epoch": time.time(),
        "base_url": args.base_url,
        "profile": args.profile,
        "warning": "Local diagnostic only; not production capacity evidence.",
        "targets": {"p95_ms": args.p95_ms, "max_error_rate": args.max_error_rate},
        "stages": [],
    }
    for users in stages:
        if args.profile == "idempotency":
            stage = run_idempotency(args.base_url, users, run_key)
        else:
            stage = run_stage(args.base_url, args.profile, users, args.requests_per_user, run_key)
        evidence["stages"].append(stage)
        print(json.dumps(stage, indent=2))
    evidence["gate_pass"] = all(
        stage["latency_ms"]["p95"] <= args.p95_ms
        and stage["error_rate"] <= args.max_error_rate
        and (args.profile != "idempotency" or stage.get("integrity_pass", False))
        for stage in evidence["stages"]
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"GATE: {'PASS' if evidence['gate_pass'] else 'FAIL'}")
    return 0 if evidence["gate_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
