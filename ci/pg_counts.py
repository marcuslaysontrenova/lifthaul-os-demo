"""Print row counts for key tables as JSON (used to verify PostgreSQL backup/restore)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import db  # noqa: E402

conn = db.connect(os.environ["DATABASE_URL"])
tables = ["tenants", "users", "customers", "bookings", "quotations",
          "audit_logs", "admin_roles", "org_units"]
out = {}
for t in tables:
    try:
        out[t] = conn.execute("SELECT COUNT(*) c FROM " + t).fetchone()["c"]
    except Exception as e:
        out[t] = "ERR:" + str(e)[:40]
print(json.dumps(out))
