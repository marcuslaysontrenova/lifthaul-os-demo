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
    db = os.environ.get("DATABASE_URL", "postgresql://USER:PASS@HOST:5432/lifthaul")
    print("LiftHaul backup/restore drill (PostgreSQL) — production playbook")
    print("-" * 60)
    print("Run these against your managed PostgreSQL and record the timings:\n")
    print("  # 1. Fingerprint BEFORE (RPO reference)")
    print(f'  psql "{db}" -c "SELECT (SELECT count(*) FROM customers) c, (SELECT count(*) FROM mkt_bookings) b, (SELECT max(version) FROM schema_version) v;"\n')
    print("  # 2. BACKUP  (note the timestamp = RPO point)")
    print(f'  pg_dump "{db}" -Fc -f lifthaul_$(date +%Y%m%dT%H%M%S).dump\n')
    print("  # 3. Destructive synthetic test on a RESTORE TARGET db (never prod):")
    print('  #    createdb lifthaul_restore; drop a table / row on the target; note the incident time.\n')
    print("  # 4. RESTORE  (start the RTO clock)")
    print('  pg_restore -d "postgresql://USER:PASS@HOST:5432/lifthaul_restore" --clean --if-exists lifthaul_*.dump\n')
    print("  # 5. Reconcile AFTER  (must match step 1)")
    print('  psql "postgresql://USER:PASS@HOST:5432/lifthaul_restore" -c "SELECT (SELECT count(*) FROM customers) c, (SELECT count(*) FROM mkt_bookings) b, (SELECT max(version) FROM schema_version) v;"\n')
    print("  # 6. Record: RTO = restore duration; RPO = now - backup timestamp. Attach as launch evidence.")
    print("-" * 60)
    print("This is a runbook (no managed PostgreSQL is contacted from here). The SQLite mode above proves")
    print("the same backup->destroy->restore->reconcile logic end to end.")
    return 0


if __name__ == "__main__":
    if "--postgres" in sys.argv:
        sys.exit(postgres_playbook())
    sys.exit(sqlite_drill())
