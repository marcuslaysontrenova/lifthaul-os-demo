"""Independent, controlled hostile probes for the 2026-09-03 LiftHaul audit.

Runs only against temporary/local SQLite state. It never contacts production,
payment providers, competitors, or real users.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import public_booking as booking  # noqa: E402


def base_payload(**changes):
    payload = {
        "contact_name": "Hostile Audit User",
        "contact_phone": "09170000000",
        "origin_island": "Luzon",
        "dest_island": "Luzon",
        "vehicle": "moto",
        "km": 10,
        "excluded_charges_ack": True,
    }
    payload.update(changes)
    return payload


def attempt(conn, payload):
    try:
        result = booking.submit(conn, payload)
        return {
            "accepted": True,
            "booking_id": result["booking_id"],
            "weight_kg": result["cargo"]["weight_kg"],
            "vehicle_match": result["vehicle_match"],
        }
    except Exception as exc:  # evidence requires the actual product response
        return {"accepted": False, "error": type(exc).__name__, "message": str(exc)}


def boundary_probe(conn):
    common = {
        "cargo_category": "BOXES_GENERAL",
        "package_count": 1,
        "package_length_cm": 100,
        "package_width_cm": 80,
        "package_height_cm": 80,
    }
    output = {}
    for weight in (909, 910):
        result = booking.recommend_vehicles(conn, base_payload(weight_kg=weight, **common))
        output[str(weight)] = [option["id"] for option in result["options"]]
    return output


def performance_probe(conn, iterations=500):
    payload = base_payload(
        cargo_category="BOXES_GENERAL",
        weight_kg=500,
        package_count=1,
        package_length_cm=100,
        package_width_cm=80,
        package_height_cm=80,
    )
    timings = []
    for _ in range(iterations):
        start = time.perf_counter()
        booking.recommend_vehicles(conn, payload)
        timings.append((time.perf_counter() - start) * 1000)
    ordered = sorted(timings)
    return {
        "iterations": iterations,
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[int(iterations * 0.95) - 1], 3),
        "max_ms": round(max(ordered), 3),
        "scope": "single-process service-function probe; not an HTTP or production load test",
    }


def duplicate_submission_probe(db_path, attempts=24):
    payload = base_payload(idempotency_key="hostile-concurrent-duplicate")

    def worker():
        conn = db.connect(str(db_path))
        try:
            return attempt(conn, payload)
        finally:
            conn.close()

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker) for _ in range(attempts)]
        for future in as_completed(futures):
            results.append(future.result())

    conn = db.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT id, tracking_token FROM mkt_bookings WHERE idempotency_key=?",
            ("hostile-concurrent-duplicate",),
        ).fetchall()
    finally:
        conn.close()
    return {
        "attempts": attempts,
        "accepted_responses": sum(1 for item in results if item["accepted"]),
        "error_responses": sum(1 for item in results if not item["accepted"]),
        "errors": sorted({item.get("message") for item in results if not item["accepted"]}),
        "persisted_rows": len(rows),
        "distinct_booking_ids_returned": len(
            {item.get("booking_id") for item in results if item["accepted"]}
        ),
    }


def main():
    # Keep the ephemeral database inside the audited workspace because this
    # environment intentionally blocks writes to the OS temp directory.
    with tempfile.TemporaryDirectory(
        prefix="_runtime-", dir=Path(__file__).resolve().parent
    ) as tmp:
        db_path = Path(tmp) / "probe.db"
        conn = db.connect(str(db_path))
        evidence = {
            "scope": "authorized local test data only",
            "missing_weight": attempt(conn, base_payload()),
            "nonnumeric_weight": attempt(conn, base_payload(weight_kg="not-a-number")),
            "incomplete_locations": attempt(
                conn,
                base_payload(
                    weight_kg=10,
                    cargo_category="BOXES_GENERAL",
                    package_count=1,
                    package_length_cm=10,
                    package_width_cm=10,
                    package_height_cm=10,
                ),
            ),
            "safety_boundary": boundary_probe(conn),
            "pricing": booking.submit(
                conn,
                base_payload(vehicle="sedan", idempotency_key="hostile-pricing"),
            )["quote_breakdown"],
            "recommendation_performance": performance_probe(conn),
        }
        conn.close()
        evidence["concurrent_duplicate_submission"] = duplicate_submission_probe(db_path)
        print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
