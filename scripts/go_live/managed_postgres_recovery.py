#!/usr/bin/env python3
"""Verify a managed PostgreSQL restore without exposing or mutating production data.

The source connection is opened read-only.  The restore target must identify a
different database instance/name.  Evidence contains only operational metadata,
row counts and a digest; it never contains connection strings or row values.

Required environment variables:
  SOURCE_DATABASE_URL, RESTORE_DATABASE_URL
  RECOVERY_OWNER, PROVIDER_BACKUP_REFERENCE, RESTORE_TARGET_REFERENCE
  RECOVERY_POINT_AT, INCIDENT_AT, RESTORE_STARTED_AT, RESTORE_FINISHED_AT
  BACKUP_RETENTION_DAYS, PITR_ENABLED=true, EVIDENCE_PATH
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


CRITICAL_TABLES = (
    "customers",
    "bookings",
    "mkt_bookings",
    "audit_logs",
    "schema_version",
)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def parse_utc(name: str) -> datetime:
    raw = required(name)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def database_identity(url: str) -> tuple[str, int | None, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise SystemExit("database URLs must use postgres:// or postgresql://")
    database = parsed.path.strip("/")
    if not parsed.hostname or not database:
        raise SystemExit("database URL must include a host and database name")
    return parsed.hostname.lower(), parsed.port, database.lower()


def validate_metadata() -> dict:
    source_url = required("SOURCE_DATABASE_URL")
    restore_url = required("RESTORE_DATABASE_URL")
    if database_identity(source_url) == database_identity(restore_url):
        raise SystemExit("restore target must be isolated from the source database")
    if required("PITR_ENABLED").lower() != "true":
        raise SystemExit("PITR_ENABLED must be explicitly true and provider-verified")
    try:
        retention_days = int(required("BACKUP_RETENTION_DAYS"))
    except ValueError as exc:
        raise SystemExit("BACKUP_RETENTION_DAYS must be an integer") from exc
    if retention_days < 1:
        raise SystemExit("BACKUP_RETENTION_DAYS must be at least 1")

    recovery_point = parse_utc("RECOVERY_POINT_AT")
    incident = parse_utc("INCIDENT_AT")
    restore_started = parse_utc("RESTORE_STARTED_AT")
    restore_finished = parse_utc("RESTORE_FINISHED_AT")
    if recovery_point > incident:
        raise SystemExit("RECOVERY_POINT_AT cannot be later than INCIDENT_AT")
    if restore_finished < restore_started:
        raise SystemExit("RESTORE_FINISHED_AT cannot be before RESTORE_STARTED_AT")
    # Distinct URLs do not establish infrastructure isolation; require separately
    # approved, operator-verified references before producing evidence.
    if required("RECOVERY_ISOLATION_APPROVAL_REF") == required("RESTORE_TARGET_REFERENCE"):
        raise SystemExit("independent isolation approval reference required")
    if required("RECOVERY_TIMESTAMP_PROVENANCE") not in {"provider-exported", "tool-observed", "operator-supplied"}:
        raise SystemExit("unsupported recovery timestamp provenance")
    # Restore a historical backup against its approved recovery-point baseline,
    # not a live source whose rows or schema may have changed after the incident.
    try:
        baseline_counts = json.loads(required("RECOVERY_APPROVED_BASELINE_COUNTS_JSON"))
    except ValueError as exc:
        raise SystemExit("approved recovery-point counts must be JSON") from exc
    if (not isinstance(baseline_counts, dict) or set(baseline_counts) != set(CRITICAL_TABLES)
            or any(type(v) is not int or v < 0 for v in baseline_counts.values())):
        raise SystemExit("approved recovery-point counts are incomplete")
    baseline_catalog_digest = required("RECOVERY_APPROVED_SCHEMA_DIGEST")
    if len(baseline_catalog_digest) != 64 or any(x not in "0123456789abcdef" for x in baseline_catalog_digest):
        raise SystemExit("approved recovery-point schema digest is invalid")
    baseline_approval_ref = required("RECOVERY_BASELINE_APPROVAL_REF")
    return {
        "approved_baseline_counts": baseline_counts,
        "approved_schema_digest": baseline_catalog_digest,
        "baseline_approval_reference": baseline_approval_ref,
        "source_url": source_url,
        "restore_url": restore_url,
        "recovery_owner": required("RECOVERY_OWNER"),
        "isolation_approval_reference": required("RECOVERY_ISOLATION_APPROVAL_REF"),
        "timestamp_provenance": required("RECOVERY_TIMESTAMP_PROVENANCE"),
        "provider_backup_reference": required("PROVIDER_BACKUP_REFERENCE"),
        "restore_target_reference": required("RESTORE_TARGET_REFERENCE"),
        "backup_retention_days": retention_days,
        "recovery_point_at": recovery_point,
        "incident_at": incident,
        "restore_started_at": restore_started,
        "restore_finished_at": restore_finished,
    }


def snapshot(conn) -> dict[str, int]:
    result: dict[str, int] = {}
    with conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        for table in CRITICAL_TABLES:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s)",
                (table,),
            )
            if not cursor.fetchone()[0]:
                raise RuntimeError(f"required table missing: {table}")
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            result[table] = int(cursor.fetchone()[0])
    return result



def schema_catalog(conn) -> tuple[str, dict[str, int]]:
    """Hash only structural catalog metadata; never select or export row values."""
    with conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_schema='public' "
            "ORDER BY table_name, ordinal_position"
        )
        columns = [tuple(row) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' ORDER BY tablename, indexname"
        )
        indexes = [tuple(row) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT table_name, constraint_name, constraint_type "
            "FROM information_schema.table_constraints "
            "WHERE table_schema='public' ORDER BY table_name, constraint_name"
        )
        constraints = [tuple(row) for row in cursor.fetchall()]
    for table in CRITICAL_TABLES:
        if not any(row[0] == table for row in columns):
            raise RuntimeError("required schema table or columns missing")
        if not any(row[0] == table for row in indexes):
            raise RuntimeError("required table index missing")
        if not any(row[0] == table and row[2] == "PRIMARY KEY" for row in constraints):
            raise RuntimeError("required table primary key missing")
    catalog = {"columns": columns, "indexes": indexes, "constraints": constraints}
    digest = hashlib.sha256(json.dumps(catalog, sort_keys=True, default=str).encode()).hexdigest()
    return digest, {"columns": len(columns), "indexes": len(indexes), "constraints": len(constraints)}


def inspect_restore_target(restore_url: str) -> tuple[dict[str, int], str, dict[str, int]]:
    """Read-only recovered target; no query against changing production source."""
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("psycopg is required for target inspection") from exc
    with psycopg.connect(restore_url) as target:
        counts = snapshot(target)
    with psycopg.connect(restore_url) as target:
        digest, catalog_counts = schema_catalog(target)
    return counts, digest, catalog_counts


def reconcile(source_url: str, restore_url: str) -> tuple[dict[str, int], dict[str, int]]:
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("psycopg is required for the managed PostgreSQL drill") from exc
    with psycopg.connect(source_url) as source, psycopg.connect(restore_url) as restored:
        return snapshot(source), snapshot(restored)


def main() -> int:
    metadata = validate_metadata()
    source_url = metadata.pop("source_url")
    restore_url = metadata.pop("restore_url")
    restored, catalog_digest, catalog_counts = inspect_restore_target(restore_url)
    if restored != metadata["approved_baseline_counts"]:
        raise SystemExit("restore reconciliation failed against approved recovery-point counts")
    if catalog_digest != metadata["approved_schema_digest"]:
        raise SystemExit("restore schema/index/constraint digest differs from approved recovery point")

    rto_seconds = (metadata["restore_finished_at"] - metadata["restore_started_at"]).total_seconds()
    rpo_seconds = (metadata["incident_at"] - metadata["recovery_point_at"]).total_seconds()
    digest = hashlib.sha256(json.dumps(restored, sort_keys=True).encode()).hexdigest()
    evidence = {
        "result": "PASS",
        "component": "read_only_approved_baseline_reconciliation_only",
        "baseline_approval_reference": metadata["baseline_approval_reference"],
        "baseline_authenticity_verified": False,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "pitr_enabled": True,
        "backup_retention_days": metadata["backup_retention_days"],
        "recovery_owner": metadata["recovery_owner"],
        "provider_backup_reference": metadata["provider_backup_reference"],
        "restore_target_reference": metadata["restore_target_reference"],
        "recovery_point_at": metadata["recovery_point_at"].isoformat(),
        "incident_at": metadata["incident_at"].isoformat(),
        "restore_started_at": metadata["restore_started_at"].isoformat(),
        "restore_finished_at": metadata["restore_finished_at"].isoformat(),
        "rto_seconds": rto_seconds,
        "rpo_seconds": rpo_seconds,
        "critical_table_counts": restored,
        "schema_catalog_digest": catalog_digest,
        "schema_catalog_counts": catalog_counts,
        "schema_catalog_compatible": True,
        "reconciliation_digest": digest,
        "source_and_restore_are_distinct": True,
        "infrastructure_isolation_verified": False,
        "witnessed_restore_verified": False,
        "provider_restore_attestation_verified": False,
        "independent_witness_signoff": "PENDING",
        "gate6_closure": "BLOCKED",
    }
    path = Path(required("EVIDENCE_PATH"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": "PASS",
        "rto_seconds": rto_seconds,
        "rpo_seconds": rpo_seconds,
        "evidence_path": str(path),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
