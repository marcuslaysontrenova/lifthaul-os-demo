"""LiftHaul OS — Configuration Definition Registry (Phase 2).

Canonical, typed, governed configuration definitions. Every Phase-2 business rule has a
definition (type, default, range, allowed scopes, snapshot requirement, risk). Values set
through admin_platform.set_config are validated against the definition when one exists.

Defaults are seeded to EXACTLY the current hardcoded constants so converting the consumers
changes no financial value.
"""
from __future__ import annotations

import datetime

import core

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_definitions(
  key TEXT PRIMARY KEY, name TEXT, description TEXT, category TEXT, data_type TEXT,
  default_value TEXT, min_value REAL, max_value REAL, allowed_values TEXT, scopes TEXT,
  snapshot_required INTEGER DEFAULT 0, risk_level TEXT DEFAULT 'low', secret INTEGER DEFAULT 0,
  deprecated INTEGER DEFAULT 0, replacement_key TEXT, version INTEGER DEFAULT 1, created_at TEXT);
"""

# (key, name, category, data_type, default, min, max, allowed(csv|None), scopes(csv), snapshot, risk)
DEFINITIONS = [
    ("quotation.approval.threshold_amount", "Approval threshold amount", "TENANT BUSINESS POLICY",
     "currency", "500000", 0, None, None, "platform,tenant,business_unit,branch", 1, "high"),
    ("quotation.approval.discount_threshold_pct", "Approval discount threshold %", "TENANT BUSINESS POLICY",
     "percent", "10", 0, 100, None, "platform,tenant,business_unit,branch", 1, "high"),
    ("tax.default.rate", "Default tax/VAT rate %", "TENANT BUSINESS POLICY",
     "percent", "12", 0, 100, None, "platform,tenant,branch", 1, "high"),
    ("tax.default.code", "Default tax code", "TENANT BUSINESS POLICY",
     "string", "VAT", None, None, None, "platform,tenant,branch", 1, "medium"),
    ("tax.rounding_mode", "Tax rounding mode", "PLATFORM POLICY",
     "enum", "round", None, None, "round,floor,ceil", "platform,tenant", 1, "medium"),
    ("payment.downpayment.default_rate", "Downpayment default %", "TENANT BUSINESS POLICY",
     "percent", "30", 0, 100, None, "platform,tenant,business_unit,branch", 1, "high"),
    ("payment.downpayment.minimum_rate", "Downpayment minimum %", "TENANT BUSINESS POLICY",
     "percent", "0", 0, 100, None, "platform,tenant,business_unit,branch", 1, "medium"),
    ("payment.downpayment.required", "Downpayment required", "TENANT BUSINESS POLICY",
     "boolean", "true", None, None, "true,false", "platform,tenant,business_unit,branch", 1, "high"),
    ("quotation.validity_days", "Quotation validity (days)", "OPERATIONAL DEFAULT",
     "integer", "30", 1, 365, None, "platform,tenant", 0, "low"),
]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    for (key, name, cat, dt, default, mn, mx, allowed, scopes, snap, risk) in DEFINITIONS:
        conn.execute(
            "INSERT INTO config_definitions(key,name,category,data_type,default_value,min_value,"
            "max_value,allowed_values,scopes,snapshot_required,risk_level,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET name=excluded.name, category=excluded.category,"
            " data_type=excluded.data_type, default_value=excluded.default_value,"
            " min_value=excluded.min_value, max_value=excluded.max_value,"
            " allowed_values=excluded.allowed_values, scopes=excluded.scopes,"
            " snapshot_required=excluded.snapshot_required, risk_level=excluded.risk_level",
            (key, name, cat, dt, default, mn, mx, allowed, scopes, snap, risk, _now()))
    conn.commit()


def get_definition(conn, key):
    try:
        return conn.execute("SELECT * FROM config_definitions WHERE key=?", (key,)).fetchone()
    except Exception:
        return None                                        # never rollback here (would undo pending work)


def list_definitions(conn):
    return conn.execute("SELECT * FROM config_definitions ORDER BY category, key").fetchall()


def validate(conn, key, value, scope=None):
    """Validate a value against its definition (if any). No definition => allowed (back-compat)."""
    d = get_definition(conn, key)
    if not d:
        return
    if d["secret"]:
        raise core.ValidationError("secret configuration cannot be set through ordinary config")
    if scope and d["scopes"] and scope not in d["scopes"].split(","):
        raise core.ValidationError(f"scope '{scope}' not permitted for '{key}' (allowed: {d['scopes']})")
    dt = d["data_type"]
    v = str(value)
    if dt in ("percent", "currency", "integer"):
        try:
            num = float(v)
        except Exception:
            raise core.ValidationError(f"'{key}' must be numeric")
        if d["min_value"] is not None and num < d["min_value"]:
            raise core.ValidationError(f"'{key}' below minimum {d['min_value']}")
        if d["max_value"] is not None and num > d["max_value"]:
            raise core.ValidationError(f"'{key}' above maximum {d['max_value']}")
    elif dt == "boolean":
        if v.lower() not in ("true", "false"):
            raise core.ValidationError(f"'{key}' must be true/false")
    elif dt == "enum":
        if d["allowed_values"] and v not in d["allowed_values"].split(","):
            raise core.ValidationError(f"'{key}' must be one of {d['allowed_values']}")
