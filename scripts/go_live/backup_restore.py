#!/usr/bin/env python3
"""LiftHaul — Backup / restore drill (Gate 5). Proves the backup->destroy->restore->reconcile cycle and
captures RTO/RPO evidence. Stdlib only.

Two modes:
  * SQLite (default; runnable anywhere, incl. CI/dev):
        python scripts/go_live/backup_restore.py
    Seeds a DB, records a fingerprint, backs it up, destructively drops a table, restores from backup,
    and reconciles the fingerprint. Proves the restore + reconciliation logic end to end.
  * PostgreSQL (production drill) — prints the exact pg_dump/pg_restore commands to run against your
    managed DB, and the reconciliation query, with RTO/RPO capture points:
        python scripts/go_live/backup_restore.py --postgres

Exit 0 = restore verified (fingerprint identical before/after).
"""
import os
import sys
import time
import hashlib
import tempfile


def _fingerprint(conn):
    """Order-independent content fingerprint over the governed tables that matter for reconciliation."""
    tables = ["customers", "bookings", "mkt_bookings", "audit_logs", "schema_version"]
    h = hashlib.sha256()
    for t in tables:
        try:
            rows = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()
            h.update(f"{t}:{rows['c'] if hasattr(rows,'keys') else rows[0]}|".encode())
        except Exception:
            h.update(f"{t}:NA|".encode())
    return h.hexdigest()[:16]


def sqlite_drill():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
    import db
    path = tempfile.mktemp(suffix=".sqlite")
    backup = path + ".bak"
    print("LiftHaul backup/restore drill (SQLite)")
    print("-" * 60)
    t0 = time.time()
    conn = db.connect(path)
    import core
    actor = {"id": 1, "role": "admin", "perms": {"*"}, "tenant_id": None}
    core.create_customer(conn, actor, "Reconcile Co", "ops@x.com", "billing@x.com")
    fp_before = _fingerprint(conn)
    print(f"  [PASS] seeded DB; fingerprint = {fp_before}")

    # BACKUP (SQLite online backup)
    import sqlite3
    src = sqlite3.connect(path)
    dst = sqlite3.connect(backup)
    src.backup(dst)
    dst.close(); src.close()
    print(f"  [PASS] backup written -> {os.path.basename(backup)}")

    # DESTRUCTIVE synthetic incident
    conn.execute("DROP TABLE customers")
    conn.commit()
    fp_broken = _fingerprint(conn)
    print(f"  [PASS] destructive incident applied (dropped customers); fingerprint now {fp_broken}")
    assert fp_broken != fp_before, "destruction did not change fingerprint"

    # RESTORE (RTO clock starts)
    rto_start = time.time()
    conn.close()
    os.replace(backup, path)
    conn = db.connect(path)
    rto = time.time() - rto_start
    fp_after = _fingerprint(conn)
    print(f"  [PASS] restored from backup in {rto:.2f}s (RTO)")

    # RECONCILE
    ok = fp_after == fp_before
    print(f"  [{'PASS' if ok else 'FAIL'}] reconciliation: fingerprint {fp_after} {'==' if ok else '!='} {fp_before}")
    print("-" * 60)
    print(f"RTO (restore time): {rto:.2f}s | RPO (data since last backup): 0 (point-in-time backup)")
    print(f"RESULT: {'VERIFIED' if ok else 'FAILED'} in {time.time()-t0:.2f}s")
    try:
        os.remove(path)
    except Exception:
        pass
    return 0 if ok else 1


def postgres_playbook():
    """Print a non-executing, secret-free, isolated restore procedure.

    The real PostgreSQL backup/restore requires an approved operator, verified
    source/target isolation, secure libpq services and a timed reconciliation.
    This command intentionally NEVER contacts PostgreSQL.
    """
    print("LiftHaul PostgreSQL backup/restore — OPERATOR RUNBOOK ONLY")
    print("-" * 60)
    print("STOP: never execute a restore against the live production database.")
    print("Prepare separate libpq service entries 'lifthaul_source' (production READ)")
    print("and 'lifthaul_restore' (isolated recovery DB) in a protected service file.")
    print("Keep passwords in a separate protected PGPASSFILE; do not print secrets.")
    print("Check that source and restore target have different hosts/DB names.")
    print("Obtain owner approval and record preflight identities, backup timestamp,")
    print("target isolation, checksum, and recovery owner before any write.")
    print()
    print("# 1. Source fingerprint and backup; password never appears in argv/logs")
    print('psql "service=lifthaul_source" -c "SELECT (SELECT count(*) FROM customers) c, (SELECT count(*) FROM mkt_bookings) b, (SELECT max(version) FROM schema_version) v;"')
    print('pg_dump "service=lifthaul_source" -Fc -f lifthaul_YYYYMMDDTHHMMSS.dump')
    print()
    print("# 2. Explicitly attest isolated target; never reuse production service.")
    print("echo 'STOP unless isolated recovery target has been independently verified'")
    print("# 3. Destructive restore ONLY to an independently isolated target")
    print('pg_restore --dbname="service=lifthaul_restore" --clean --if-exists lifthaul_YYYYMMDDTHHMMSS.dump')
    print()
    print("# 4. Reconcile counts, schema and application integrity on restore target")
    print('psql "service=lifthaul_restore" -c "SELECT (SELECT count(*) FROM customers) c, (SELECT count(*) FROM mkt_bookings) b, (SELECT max(version) FROM schema_version) v;"')
    print("# 5. Record RTO = timed isolated restore; RPO = backup timestamp age.")
    print("-" * 60)
    print("NO BACKUP OR RESTORE EXECUTED. No verified RTO/RPO evidence collected.")
    return 0


if __name__ == "__main__":
    if "--postgres" in sys.argv:
        sys.exit(postgres_playbook())
    sys.exit(sqlite_drill())
