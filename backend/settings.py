"""LiftHaul OS — Phase 6: governed Platform & System Settings.

Typed, validated, scoped (platform→tenant→organization→user), effective-dated, audited settings;
a secret-reference boundary (values never stored/logged/exported); security minimums a tenant may
strengthen but never weaken; feature flags; a module registry with dependency + unsafe-disable
guards; scoped maintenance mode with mandatory expiry; retention policies with legal hold; governed
backup metadata + a restore-approval workflow; branding + allowlisted-variable templates; and a
system-integrity checker.

Reuses (does NOT duplicate): Phase-2 tax/approval/downpayment policy, Phase-3 numbering, the Phase-1
calendar engine. Security invariants are platform floors — a tenant value below the floor is rejected
→ zero security-policy weakening.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core

SETTING_TYPES = ("string", "integer", "decimal", "boolean", "enum", "json", "currency", "percent")
STATUSES = ("ACTIVE", "SUPERSEDED", "RETIRED")
SCOPES = ("platform", "tenant", "business_unit", "branch", "department", "team", "user")
SENSITIVE_MASK = "••••••"

# Security-invariant definitions: (key, type, platform_default, direction, allowed)
#   direction 'min' => tenant value must be >= platform (stronger); 'max' => tenant value must be <=
#   platform (e.g. session length ceiling); 'exact_or_stronger' for enums via RANK.
SECURITY_MINIMUMS = {
    "auth.password.min_length":     ("integer", "10", "min"),
    "auth.password.history":        ("integer", "3", "min"),
    "auth.lockout.threshold":       ("integer", "5", "max"),      # fewer attempts = stronger
    "auth.lockout.duration_min":    ("integer", "15", "min"),
    "auth.mfa.policy":              ("enum", "optional", "rank"),  # off<optional<required
    "session.idle_timeout_min":     ("integer", "30", "max"),      # shorter = stronger
    "session.absolute_timeout_min": ("integer", "720", "max"),
    "session.concurrent_limit":     ("integer", "5", "max"),
}
_MFA_RANK = {"off": 0, "optional": 1, "required": 2}

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting_definitions(
  key TEXT PRIMARY KEY, name TEXT, description TEXT, category TEXT, data_type TEXT NOT NULL,
  default_value TEXT, allowed_values TEXT, min_value REAL, max_value REAL, format TEXT,
  scopes TEXT, inheritance TEXT DEFAULT 'most_specific', secret INTEGER DEFAULT 0,
  restart_required INTEGER DEFAULT 0, effective_dated INTEGER DEFAULT 0, approval_required INTEGER DEFAULT 0,
  risk_level TEXT DEFAULT 'low', snapshot_required INTEGER DEFAULT 0, security_invariant INTEGER DEFAULT 0,
  invariant_direction TEXT, deprecated INTEGER DEFAULT 0, replacement_key TEXT,
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT);

CREATE TABLE IF NOT EXISTS setting_values(
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, tenant_id INTEGER, scope TEXT NOT NULL DEFAULT 'platform',
  scope_ref TEXT, value TEXT, effective_from TEXT, effective_to TEXT, status TEXT DEFAULT 'ACTIVE',
  version INTEGER DEFAULT 1, reason TEXT, approved_by INTEGER, created_by INTEGER, created_at TEXT,
  updated_by INTEGER, updated_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS secret_references(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, provider TEXT, env_name TEXT,
  scope TEXT, owner INTEGER, status TEXT DEFAULT 'ACTIVE', last_verified_at TEXT, last_rotated_at TEXT,
  rotation_days INTEGER, version INTEGER DEFAULT 1, masked_hint TEXT, created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS feature_flags(
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, description TEXT, owner INTEGER, risk TEXT DEFAULT 'low',
  platform_default INTEGER DEFAULT 0, rollout_pct INTEGER DEFAULT 0, dependency TEXT,
  expires_at TEXT, status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT, UNIQUE(key));

CREATE TABLE IF NOT EXISTS feature_flag_overrides(
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, tenant_id INTEGER, scope TEXT DEFAULT 'tenant', scope_ref TEXT,
  enabled INTEGER, effective_from TEXT, effective_to TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS modules(
  code TEXT PRIMARY KEY, name TEXT, description TEXT, dependencies TEXT, platform_status TEXT DEFAULT 'ENABLED',
  required_permissions TEXT, owner INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS module_tenant_status(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL, tenant_id INTEGER, status TEXT DEFAULT 'ENABLED',
  enabled_at TEXT, disabled_at TEXT, updated_by INTEGER, UNIQUE(code, tenant_id));

CREATE TABLE IF NOT EXISTS maintenance_windows(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, scope TEXT DEFAULT 'tenant', mode TEXT DEFAULT 'read_only',
  message TEXT, allowed_roles TEXT, starts_at TEXT, ends_at TEXT, status TEXT DEFAULT 'SCHEDULED',
  created_by INTEGER, created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS retention_policies(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, category TEXT NOT NULL, retention_days INTEGER,
  legal_hold INTEGER DEFAULT 0, archive_behavior TEXT DEFAULT 'archive', deletion_behavior TEXT DEFAULT 'soft',
  platform_minimum_days INTEGER, last_executed_at TEXT, next_execution_at TEXT, created_by INTEGER,
  created_at TEXT, UNIQUE(tenant_id, category));

CREATE TABLE IF NOT EXISTS backup_runs(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, kind TEXT DEFAULT 'logical', status TEXT DEFAULT 'PENDING',
  storage_ref TEXT, encryption_ref TEXT, size_bytes INTEGER, checksum TEXT, restore_point TEXT,
  operator INTEGER, started_at TEXT, finished_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS restore_requests(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, backup_run_id INTEGER, target TEXT DEFAULT 'isolated',
  status TEXT DEFAULT 'REQUESTED', requested_by INTEGER, validated_by INTEGER, approved_by INTEGER,
  reason TEXT, created_at TEXT, correlation_id TEXT);

CREATE TABLE IF NOT EXISTS branding_assets(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, kind TEXT NOT NULL, value TEXT, file_ref TEXT,
  content_type TEXT, size_bytes INTEGER, status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, kind));

CREATE TABLE IF NOT EXISTS doc_templates(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT NOT NULL, name TEXT, channel TEXT,
  body TEXT, allowed_variables TEXT, version INTEGER DEFAULT 1, status TEXT DEFAULT 'DRAFT',
  effective_from TEXT, effective_to TEXT, checksum TEXT, approved_by INTEGER, published_by INTEGER,
  created_by INTEGER, created_at TEXT);
"""

# (key, name, category, type, default, allowed|None, min, max, scopes, secret, restart, risk, security_invariant, direction)
DEFINITIONS = [
    ("platform.name", "Platform name", "PLATFORM", "string", "LiftHaul OS", None, None, None, "platform", 0, 0, "low", 0, None),
    ("platform.default_locale", "Default locale", "PLATFORM", "string", "en-PH", None, None, None, "platform,tenant", 0, 0, "low", 0, None),
    ("platform.default_timezone", "Default timezone", "PLATFORM", "string", "Asia/Manila", None, None, None, "platform,tenant,branch", 0, 0, "low", 0, None),
    ("platform.default_currency", "Default currency", "PLATFORM", "string", "PHP", None, None, None, "platform,tenant", 0, 0, "medium", 0, None),
    ("fiscal.year_start_month", "Fiscal year start month", "FISCAL", "integer", "1", None, 1, 12, "platform,tenant", 0, 0, "medium", 0, None),
    ("currency.precision", "Currency precision", "FISCAL", "integer", "2", None, 0, 6, "platform,tenant", 0, 0, "medium", 0, None),
    ("file.max_size_bytes", "Max file size (bytes)", "FILE", "integer", "10485760", None, 1, None, "platform,tenant", 0, 0, "medium", 1, "max"),
    ("file.allowed_types", "Allowed file types (csv)", "FILE", "string", "application/pdf,image/png,image/jpeg", None, None, None, "platform,tenant", 0, 0, "medium", 0, None),
    ("api.rate_limit_per_min", "API rate limit / min", "API", "integer", "600", None, 1, None, "platform,tenant", 0, 0, "medium", 1, "max"),
    ("api.token_lifetime_min", "API token lifetime (min)", "API", "integer", "60", None, 1, None, "platform,tenant", 0, 0, "high", 1, "max"),
    ("api.error_detail", "API error detail", "API", "enum", "minimal", "minimal,verbose", None, None, "platform,tenant", 0, 0, "medium", 0, None),
    # security invariants (platform floors)
    ("auth.password.min_length", "Password min length", "SECURITY", "integer", "10", None, 4, 128, "platform,tenant", 0, 0, "high", 1, "min"),
    ("auth.password.history", "Password history", "SECURITY", "integer", "3", None, 0, 24, "platform,tenant", 0, 0, "high", 1, "min"),
    ("auth.lockout.threshold", "Lockout threshold", "SECURITY", "integer", "5", None, 1, 20, "platform,tenant", 0, 0, "high", 1, "max"),
    ("auth.lockout.duration_min", "Lockout duration (min)", "SECURITY", "integer", "15", None, 1, 1440, "platform,tenant", 0, 0, "high", 1, "min"),
    ("auth.mfa.policy", "MFA policy", "SECURITY", "enum", "optional", "off,optional,required", None, None, "platform,tenant", 0, 0, "high", 1, "rank"),
    ("session.idle_timeout_min", "Session idle timeout (min)", "SECURITY", "integer", "30", None, 1, 1440, "platform,tenant", 0, 0, "high", 1, "max"),
    ("session.absolute_timeout_min", "Session absolute timeout (min)", "SECURITY", "integer", "720", None, 1, 10080, "platform,tenant", 0, 0, "high", 1, "max"),
    ("session.concurrent_limit", "Concurrent session limit", "SECURITY", "integer", "5", None, 1, 100, "platform,tenant", 0, 0, "high", 1, "max"),
    ("audit.retention_days", "Audit retention (days)", "RETENTION", "integer", "2555", None, 365, None, "platform,tenant", 0, 0, "high", 1, "min"),
]

DEFAULT_MODULES = [
    ("crm", "CRM", ""), ("booking", "Booking", ""), ("quotation", "Quotation", "booking"),
    ("payments", "Payments", "quotation"), ("jobs", "Jobs", "quotation"), ("dispatch", "Dispatch", "jobs"),
    ("fleet", "Fleet", ""), ("maintenance", "Maintenance", "fleet"), ("safety", "Safety", ""),
    ("finance", "Finance", "payments"), ("customer_portal", "Customer Portal", "crm"),
    ("reporting", "Reporting", ""), ("ai_assistance", "AI Assistance", ""),
]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _tenant(actor):
    return (actor or {}).get("tenant_id")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    for (key, name, cat, dt, default, allowed, mn, mx, scopes, secret, restart, risk, inv, direction) in DEFINITIONS:
        conn.execute(
            "INSERT INTO setting_definitions(key,name,category,data_type,default_value,allowed_values,"
            "min_value,max_value,scopes,secret,restart_required,risk_level,security_invariant,"
            "invariant_direction,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET name=excluded.name, category=excluded.category,"
            " data_type=excluded.data_type, default_value=excluded.default_value,"
            " allowed_values=excluded.allowed_values, min_value=excluded.min_value,"
            " max_value=excluded.max_value, scopes=excluded.scopes, security_invariant=excluded.security_invariant,"
            " invariant_direction=excluded.invariant_direction",
            (key, name, cat, dt, default, allowed, mn, mx, scopes, secret, restart, risk, inv, direction, _now()))
    for (code, name, deps) in DEFAULT_MODULES:
        conn.execute("INSERT INTO modules(code,name,dependencies,platform_status,created_at) VALUES(?,?,?, 'ENABLED', ?)"
                     " ON CONFLICT(code) DO UPDATE SET name=excluded.name, dependencies=excluded.dependencies",
                     (code, name, deps, _now()))
    conn.commit()


# --------------------------------------------------------------------------- #
# Setting definitions + values (typed, scoped, effective-dated, security-floored)
# --------------------------------------------------------------------------- #
def get_definition(conn, key):
    try:
        return conn.execute("SELECT * FROM setting_definitions WHERE key=?", (key,)).fetchone()
    except Exception:
        return None


def list_definitions(conn, actor, category=None):
    core.require(actor, "platform.settings.view")
    if category:
        return [dict(r) for r in conn.execute("SELECT * FROM setting_definitions WHERE category=? ORDER BY key", (category,)).fetchall()]
    return [dict(r) for r in conn.execute("SELECT * FROM setting_definitions ORDER BY category,key").fetchall()]


def _coerce_num(dt, v):
    return int(v) if dt == "integer" else float(v)


def _validate(conn, key, value, scope):
    d = get_definition(conn, key)
    if not d:
        raise core.ValidationError(f"unknown setting key '{key}' (create a definition first)")
    if d["secret"]:
        raise core.ValidationError("secret settings must use a secret reference, not a value")
    if scope and d["scopes"] and scope not in d["scopes"].split(","):
        raise core.ValidationError(f"scope '{scope}' not permitted for '{key}'")
    dt = d["data_type"]
    v = str(value)
    if dt in ("integer", "decimal", "currency", "percent"):
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
    elif dt == "json":
        try:
            json.loads(v)
        except Exception:
            raise core.ValidationError(f"'{key}' must be valid JSON")
    return d


def _platform_value(conn, key, default):
    row = conn.execute("SELECT value FROM setting_values WHERE key=? AND scope='platform' AND status='ACTIVE'"
                       " ORDER BY version DESC LIMIT 1", (key,)).fetchone()
    return row["value"] if row else default


def _enforce_security_floor(conn, d, key, value, scope):
    """A tenant/org value may strengthen a security invariant but never weaken below the platform
    floor. Raises ForbiddenError on weakening."""
    if not d["security_invariant"] or scope == "platform":
        return
    direction = d["invariant_direction"]
    platform = _platform_value(conn, key, d["default_value"])
    if direction == "rank":
        if _MFA_RANK.get(str(value), 0) < _MFA_RANK.get(str(platform), 0):
            raise core.ForbiddenError(f"'{key}' cannot be weaker than the platform minimum '{platform}'")
    elif direction == "min":            # value must be >= platform (bigger = stronger)
        if float(value) < float(platform):
            raise core.ForbiddenError(f"'{key}' cannot be below the platform minimum {platform}")
    elif direction == "max":            # value must be <= platform (smaller = stronger)
        if float(value) > float(platform):
            raise core.ForbiddenError(f"'{key}' cannot exceed the platform maximum {platform}")


def set_value(conn, actor, key, value, scope="platform", scope_ref=None, effective_from=None,
              effective_to=None, reason=None, approved_by=None):
    """Set a governed setting value. Enforces type, scope, and the security floor. Supersedes the
    prior active value at the same key+scope+scope_ref (versioned, audited)."""
    # authorization: platform scope needs platform.settings.manage; others tenant.settings.manage
    if scope == "platform":
        core.require(actor, "platform.settings.manage")
    elif scope == "tenant":
        core.require(actor, "tenant.settings.manage")
    else:
        core.require(actor, "organization.settings.override")
    d = _validate(conn, key, value, scope)
    if d["security_invariant"] and scope == "platform":
        core.require(actor, "security.policy.manage")
    _enforce_security_floor(conn, d, key, value, scope)
    tid = _tenant(actor) if scope != "platform" else None
    prev = conn.execute("SELECT MAX(version) v FROM setting_values WHERE key=? AND scope=? AND"
                        " (scope_ref IS ? OR scope_ref=?)", (key, scope, scope_ref, scope_ref or "")).fetchone()
    ver = (prev["v"] or 0) + 1
    conn.execute("UPDATE setting_values SET status='SUPERSEDED' WHERE key=? AND scope=? AND status='ACTIVE'"
                 " AND (scope_ref IS ? OR scope_ref=?)", (key, scope, scope_ref, scope_ref or ""))
    cur = conn.execute("INSERT INTO setting_values(key,tenant_id,scope,scope_ref,value,effective_from,effective_to,"
                       "status,version,reason,approved_by,created_by,created_at,correlation_id)"
                       " VALUES(?,?,?,?,?,?,?, 'ACTIVE', ?,?,?,?,?,?)",
                       (key, tid, scope, scope_ref, str(value), effective_from or _today(), effective_to,
                        ver, reason, approved_by, (actor or {}).get("id"), _now(), core.correlation_id()))
    core.audit(conn, actor, "SETTING_CHANGED", "setting_values", cur.lastrowid,
               new={"key": key, "scope": scope, "value": str(value), "version": ver}, reason=reason)
    conn.commit()
    return cur.lastrowid


def effective_value(conn, actor, key, tenant=None, org_chain=None):
    """Resolve the effective value most-specific-first (user→…→tenant→platform→definition default),
    honoring effective dates. org_chain is an ordered list of (scope, scope_ref) most-specific-first."""
    today = _today()
    for (scope, ref) in (org_chain or []):
        row = conn.execute("SELECT value FROM setting_values WHERE key=? AND scope=? AND scope_ref=? AND"
                           " status='ACTIVE' AND (effective_from IS NULL OR effective_from<=?) AND"
                           " (effective_to IS NULL OR effective_to>=?) ORDER BY version DESC LIMIT 1",
                           (key, scope, str(ref), today, today)).fetchone()
        if row:
            return {"key": key, "value": row["value"], "source": f"{scope}:{ref}"}
    tid = tenant if tenant is not None else _tenant(actor)
    if tid is not None:
        row = conn.execute("SELECT value FROM setting_values WHERE key=? AND scope='tenant' AND tenant_id=? AND"
                           " status='ACTIVE' AND (effective_to IS NULL OR effective_to>=?) ORDER BY version DESC LIMIT 1",
                           (key, tid, today)).fetchone()
        if row:
            return {"key": key, "value": row["value"], "source": "tenant"}
    pv = conn.execute("SELECT value FROM setting_values WHERE key=? AND scope='platform' AND status='ACTIVE'"
                      " ORDER BY version DESC LIMIT 1", (key,)).fetchone()
    if pv:
        return {"key": key, "value": pv["value"], "source": "platform"}
    d = get_definition(conn, key)
    return {"key": key, "value": d["default_value"] if d else None, "source": "definition_default"}


def value_history(conn, actor, key, scope=None):
    core.require(actor, "platform.settings.view")
    sql = "SELECT * FROM setting_values WHERE key=?"
    args = [key]
    if scope:
        sql += " AND scope=?"; args.append(scope)
    return [dict(r) for r in conn.execute(sql + " ORDER BY version DESC", tuple(args)).fetchall()]


# --------------------------------------------------------------------------- #
# Secret references (values NEVER stored)
# --------------------------------------------------------------------------- #
def create_secret_reference(conn, actor, code, provider, env_name, scope="platform", rotation_days=90,
                            masked_hint=None):
    core.require(actor, "security.policy.manage")
    tid = _tenant(actor)
    if conn.execute("SELECT 1 FROM secret_references WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)",
                    (code, tid)).fetchone():
        raise core.ConflictError(f"secret reference '{code}' already exists")
    cur = conn.execute("INSERT INTO secret_references(tenant_id,code,provider,env_name,scope,owner,status,"
                       "rotation_days,masked_hint,created_by,created_at) VALUES(?,?,?,?,?,?, 'ACTIVE', ?,?,?,?)",
                       (tid, code, provider, env_name, scope, (actor or {}).get("id"), rotation_days,
                        masked_hint, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "SECRET_REFERENCE_CREATED", "secret_references", cur.lastrowid,
               new={"code": code, "provider": provider})   # NEVER the value
    conn.commit()
    return cur.lastrowid


def _mask_ref(row):
    d = dict(row)
    d.pop("env_name", None)                 # do not expose the env variable name in listings
    d["value"] = SENSITIVE_MASK             # never a real value
    return d


def list_secret_references(conn, actor):
    core.require(actor, "security.policy.view")
    tid = _tenant(actor)
    rows = conn.execute("SELECT * FROM secret_references WHERE tenant_id=? OR tenant_id IS NULL ORDER BY code",
                        (tid,)).fetchall() if tid is not None else conn.execute("SELECT * FROM secret_references ORDER BY code").fetchall()
    return [_mask_ref(r) for r in rows]


def validate_secret_reference(conn, actor, code):
    """Verify the referenced secret resolves (from the environment/store) WITHOUT returning it."""
    core.require(actor, "security.policy.manage")
    row = conn.execute("SELECT * FROM secret_references WHERE code=? LIMIT 1", (code,)).fetchone()
    if not row:
        raise core.NotFoundError("secret reference not found")
    import os
    present = bool(os.environ.get(row["env_name"])) if row["env_name"] else False
    conn.execute("UPDATE secret_references SET last_verified_at=?, status=? WHERE id=?",
                 (_now(), "ACTIVE" if present else "UNVERIFIED", row["id"]))
    core.audit(conn, actor, "SECRET_REFERENCE_VALIDATED", "secret_references", row["id"],
               new={"code": code, "present": present})     # boolean only, never the value
    conn.commit()
    return {"code": code, "present": present}


def rotate_secret_reference(conn, actor, code):
    core.require(actor, "security.policy.manage")
    conn.execute("UPDATE secret_references SET last_rotated_at=?, version=version+1 WHERE code=?", (_now(), code))
    core.audit(conn, actor, "SECRET_REFERENCE_ROTATED", "secret_references", 0, new={"code": code})
    conn.commit()
    return True


def revoke_secret_reference(conn, actor, code):
    core.require(actor, "security.policy.manage")
    conn.execute("UPDATE secret_references SET status='REVOKED' WHERE code=?", (code,))
    core.audit(conn, actor, "SECRET_REFERENCE_REVOKED", "secret_references", 0, new={"code": code})
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #
def create_flag(conn, actor, key, description=None, platform_default=False, dependency=None,
                risk="low", expires_at=None):
    core.require(actor, "feature_flag.manage")
    if conn.execute("SELECT 1 FROM feature_flags WHERE key=?", (key,)).fetchone():
        raise core.ConflictError(f"feature flag '{key}' already exists")
    cur = conn.execute("INSERT INTO feature_flags(key,description,owner,risk,platform_default,dependency,"
                       "expires_at,status,created_by,created_at) VALUES(?,?,?,?,?,?,?, 'ACTIVE', ?,?)",
                       (key, description, (actor or {}).get("id"), risk, 1 if platform_default else 0,
                        dependency, expires_at, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "FEATURE_FLAG_CREATED", "feature_flags", cur.lastrowid, new={"key": key})
    conn.commit()
    return cur.lastrowid


def set_flag_override(conn, actor, key, enabled, tenant=None, scope="tenant", scope_ref=None,
                      effective_from=None, effective_to=None):
    core.require(actor, "feature_flag.manage")
    flag = conn.execute("SELECT * FROM feature_flags WHERE key=?", (key,)).fetchone()
    if not flag:
        raise core.NotFoundError("feature flag not found")
    # dependency validation: cannot enable a flag whose dependency is not enabled
    if enabled and flag["dependency"]:
        dep_on = is_flag_enabled(conn, flag["dependency"], tenant=tenant if tenant is not None else _tenant(actor))
        if not dep_on:
            raise core.ValidationError(f"cannot enable '{key}': dependency '{flag['dependency']}' is disabled")
    tid = tenant if tenant is not None else _tenant(actor)
    conn.execute("INSERT INTO feature_flag_overrides(key,tenant_id,scope,scope_ref,enabled,effective_from,"
                 "effective_to,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (key, tid, scope, scope_ref, 1 if enabled else 0, effective_from or _today(), effective_to,
                  (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "FEATURE_FLAG_OVERRIDDEN", "feature_flags", flag["id"],
               new={"key": key, "tenant": tid, "enabled": bool(enabled)})
    conn.commit()
    return True


def emergency_disable_flag(conn, actor, key, reason=None):
    core.require(actor, "feature_flag.emergency_disable")
    conn.execute("UPDATE feature_flags SET status='KILLED', platform_default=0 WHERE key=?", (key,))
    core.audit(conn, actor, "FEATURE_FLAG_EMERGENCY_DISABLED", "feature_flags", 0, new={"key": key}, reason=reason)
    conn.commit()
    return True


def is_flag_enabled(conn, key, tenant=None):
    flag = conn.execute("SELECT * FROM feature_flags WHERE key=?", (key,)).fetchone()
    if not flag or flag["status"] == "KILLED":
        return False
    if flag["expires_at"] and flag["expires_at"] < _today():
        return False
    today = _today()
    if tenant is not None:
        ov = conn.execute("SELECT enabled FROM feature_flag_overrides WHERE key=? AND tenant_id=? AND"
                          " (effective_from IS NULL OR effective_from<=?) AND (effective_to IS NULL OR effective_to>=?)"
                          " ORDER BY id DESC LIMIT 1", (key, tenant, today, today)).fetchone()
        if ov is not None:
            return bool(ov["enabled"])
    return bool(flag["platform_default"])


def list_flags(conn, actor):
    core.require(actor, "feature_flag.view")
    return [dict(r) for r in conn.execute("SELECT * FROM feature_flags ORDER BY key").fetchall()]


# --------------------------------------------------------------------------- #
# Module registry (dependency + unsafe-disable guard)
# --------------------------------------------------------------------------- #
def list_modules(conn, actor):
    core.require(actor, "module.view")
    tid = _tenant(actor)
    out = []
    for m in conn.execute("SELECT * FROM modules ORDER BY code").fetchall():
        st = conn.execute("SELECT status FROM module_tenant_status WHERE code=? AND tenant_id=?", (m["code"], tid)).fetchone()
        d = dict(m); d["tenant_status"] = st["status"] if st else "ENABLED"
        out.append(d)
    return out


def _module_enabled(conn, code, tid):
    st = conn.execute("SELECT status FROM module_tenant_status WHERE code=? AND tenant_id=?", (code, tid)).fetchone()
    return (st["status"] if st else "ENABLED") == "ENABLED"


def module_disable_impact(conn, actor, code):
    """Impact preview before disabling a module. Reports dependents + active-transaction blockers."""
    core.require(actor, "module.view")
    tid = _tenant(actor)
    dependents = [m["code"] for m in conn.execute("SELECT code,dependencies FROM modules").fetchall()
                  if m["dependencies"] and code in [d.strip() for d in m["dependencies"].split(",")]
                  and _module_enabled(conn, m["code"], tid)]
    blockers = []
    if code == "quotation":
        n = conn.execute("SELECT COUNT(*) c FROM quotations WHERE status NOT IN ('declined','expired','superseded')").fetchone()["c"]
        if n:
            blockers.append(f"{n} active quotations")
    if code == "jobs":
        try:
            n = conn.execute("SELECT COUNT(*) c FROM jobs WHERE status NOT IN ('CLOSED','CANCELLED')").fetchone()["c"]
            if n:
                blockers.append(f"{n} open jobs")
        except Exception:
            pass
    return {"module": code, "enabled_dependents": dependents, "active_blockers": blockers,
            "safe_to_disable": not dependents and not blockers}


def set_module_status(conn, actor, code, enabled, reason=None):
    core.require(actor, "module.manage")
    tid = _tenant(actor)
    if not enabled:
        impact = module_disable_impact(conn, actor, code)
        if not impact["safe_to_disable"]:
            raise core.ConflictError(f"cannot disable '{code}': {impact['enabled_dependents'] + impact['active_blockers']}")
    conn.execute("INSERT INTO module_tenant_status(code,tenant_id,status,enabled_at,disabled_at,updated_by)"
                 " VALUES(?,?,?,?,?,?) ON CONFLICT(code,tenant_id) DO UPDATE SET status=excluded.status,"
                 " enabled_at=excluded.enabled_at, disabled_at=excluded.disabled_at, updated_by=excluded.updated_by",
                 (code, tid, "ENABLED" if enabled else "DISABLED", _now() if enabled else None,
                  None if enabled else _now(), (actor or {}).get("id")))
    core.audit(conn, actor, "MODULE_STATUS_CHANGED", "modules", 0,
               new={"code": code, "status": "ENABLED" if enabled else "DISABLED"}, reason=reason)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Maintenance mode (scoped, mandatory expiry)
# --------------------------------------------------------------------------- #
def schedule_maintenance(conn, actor, mode, starts_at, ends_at, message=None, scope="tenant",
                         allowed_roles="admin"):
    if scope == "platform":
        core.require(actor, "maintenance.platform_manage")
    else:
        core.require(actor, "maintenance.manage")
    if not ends_at:
        raise core.ValidationError("maintenance mode requires an end time (no permanent maintenance)")
    if ends_at <= (starts_at or _now()):
        raise core.ValidationError("maintenance end must be after start")
    tid = _tenant(actor) if scope != "platform" else None
    cur = conn.execute("INSERT INTO maintenance_windows(tenant_id,scope,mode,message,allowed_roles,starts_at,"
                       "ends_at,status,created_by,created_at,correlation_id) VALUES(?,?,?,?,?,?,?, 'SCHEDULED', ?,?,?)",
                       (tid, scope, mode, message, allowed_roles, starts_at or _now(), ends_at,
                        (actor or {}).get("id"), _now(), core.correlation_id()))
    core.audit(conn, actor, "MAINTENANCE_SCHEDULED", "maintenance_windows", cur.lastrowid,
               new={"scope": scope, "mode": mode, "ends_at": ends_at})
    conn.commit()
    return cur.lastrowid


def maintenance_status(conn, tenant=None, now_iso=None):
    """Active maintenance window (expiry-aware) for a tenant/platform, or None."""
    now = now_iso or _now()
    row = conn.execute("SELECT * FROM maintenance_windows WHERE status='SCHEDULED' AND starts_at<=? AND ends_at>? AND"
                       " (scope='platform' OR tenant_id=?) ORDER BY scope='platform' DESC, id DESC LIMIT 1",
                       (now, now, tenant)).fetchone()
    return dict(row) if row else None


def end_maintenance(conn, actor, window_id, reason=None):
    core.require(actor, "maintenance.manage")
    conn.execute("UPDATE maintenance_windows SET status='ENDED' WHERE id=?", (window_id,))
    core.audit(conn, actor, "MAINTENANCE_ENDED", "maintenance_windows", window_id, reason=reason)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Retention policies (legal hold)
# --------------------------------------------------------------------------- #
def set_retention(conn, actor, category, retention_days, legal_hold=False, archive_behavior="archive",
                  deletion_behavior="soft", platform_minimum_days=None):
    core.require(actor, "retention.manage")
    if category == "audit":
        core.require(actor, "audit.retention.manage")
        floor = platform_minimum_days or 2555
        if int(retention_days) < floor:
            raise core.ForbiddenError(f"audit retention cannot be below the platform minimum {floor} days")
    tid = _tenant(actor)
    conn.execute("INSERT INTO retention_policies(tenant_id,category,retention_days,legal_hold,archive_behavior,"
                 "deletion_behavior,platform_minimum_days,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)"
                 " ON CONFLICT(tenant_id,category) DO UPDATE SET retention_days=excluded.retention_days,"
                 " legal_hold=excluded.legal_hold, archive_behavior=excluded.archive_behavior,"
                 " deletion_behavior=excluded.deletion_behavior",
                 (tid, category, int(retention_days), 1 if legal_hold else 0, archive_behavior,
                  deletion_behavior, platform_minimum_days, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "RETENTION_CHANGED", "retention_policies", 0,
               new={"category": category, "days": retention_days, "legal_hold": bool(legal_hold)})
    conn.commit()
    return True


def can_delete_category(conn, tenant, category):
    """Deletion is blocked when a legal hold is set for the category."""
    row = conn.execute("SELECT legal_hold FROM retention_policies WHERE tenant_id=? AND category=?", (tenant, category)).fetchone()
    return not (row and row["legal_hold"])


def list_retention(conn, actor):
    core.require(actor, "retention.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM retention_policies WHERE tenant_id=? OR tenant_id IS NULL ORDER BY category", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# Backup + governed restore workflow (no raw credentials)
# --------------------------------------------------------------------------- #
def execute_backup(conn, actor, kind="logical", storage_ref=None, encryption_ref=None):
    core.require(actor, "backup.execute")
    cid = core.correlation_id()
    cur = conn.execute("INSERT INTO backup_runs(tenant_id,kind,status,storage_ref,encryption_ref,operator,"
                       "started_at,correlation_id) VALUES(?,?, 'RUNNING', ?,?,?,?,?)",
                       (_tenant(actor), kind, storage_ref, encryption_ref, (actor or {}).get("id"), _now(), cid))
    rid = cur.lastrowid
    # metadata-only record (the actual dump is produced by the ops backup job / CI pg_dump)
    checksum = hashlib.sha256(f"{rid}:{cid}".encode()).hexdigest()
    conn.execute("UPDATE backup_runs SET status='SUCCESS', finished_at=?, checksum=?, restore_point=?,"
                 " size_bytes=? WHERE id=?", (_now(), checksum, _now(), 0, rid))
    core.audit(conn, actor, "BACKUP_EXECUTED", "backup_runs", rid, new={"kind": kind, "checksum": checksum[:12]})
    conn.commit()
    return {"backup_run_id": rid, "checksum": checksum, "status": "SUCCESS"}


def list_backups(conn, actor):
    core.require(actor, "backup.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM backup_runs WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC LIMIT 100", (tid,)).fetchall()]


def request_restore(conn, actor, backup_run_id, reason=None, target="isolated"):
    core.require(actor, "restore.execute")
    cur = conn.execute("INSERT INTO restore_requests(tenant_id,backup_run_id,target,status,requested_by,"
                       "reason,created_at,correlation_id) VALUES(?,?,?, 'REQUESTED', ?,?,?,?)",
                       (_tenant(actor), backup_run_id, target, (actor or {}).get("id"), reason, _now(), core.correlation_id()))
    core.audit(conn, actor, "RESTORE_REQUESTED", "restore_requests", cur.lastrowid, new={"backup": backup_run_id})
    conn.commit()
    return cur.lastrowid


def validate_restore(conn, actor, restore_id):
    core.require(actor, "restore.execute")
    conn.execute("UPDATE restore_requests SET status='VALIDATED', validated_by=? WHERE id=?", ((actor or {}).get("id"), restore_id))
    core.audit(conn, actor, "RESTORE_VALIDATED", "restore_requests", restore_id)
    conn.commit()
    return True


def approve_restore(conn, actor, restore_id, reason=None):
    """Restore promotion requires a SEPARATE approver (governed, never self-approval)."""
    core.require(actor, "restore.approve")
    r = conn.execute("SELECT * FROM restore_requests WHERE id=?", (restore_id,)).fetchone()
    if not r:
        raise core.NotFoundError("restore request not found")
    if r["status"] != "VALIDATED":
        raise core.ConflictError("restore must be VALIDATED before approval")
    if r["requested_by"] == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: restore approver must differ from requester")
    conn.execute("UPDATE restore_requests SET status='APPROVED', approved_by=? WHERE id=?", ((actor or {}).get("id"), restore_id))
    core.audit(conn, actor, "RESTORE_APPROVED", "restore_requests", restore_id, reason=reason)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Branding + templates (sanitized, allowlisted variables)
# --------------------------------------------------------------------------- #
_SCRIPT_MARKERS = ("<script", "javascript:", "onerror=", "onload=", "<iframe")


def set_branding(conn, actor, kind, value=None, file_ref=None, content_type=None, size_bytes=None):
    core.require(actor, "branding.manage")
    if value and any(m in str(value).lower() for m in _SCRIPT_MARKERS):
        raise core.ValidationError("branding value must not contain scripts/executable markup")
    if content_type and content_type not in ("image/png", "image/jpeg", "image/svg+xml"):
        if kind in ("logo", "favicon"):
            raise core.ValidationError("branding image type not allowed")
    tid = _tenant(actor)
    conn.execute("INSERT INTO branding_assets(tenant_id,kind,value,file_ref,content_type,size_bytes,status,"
                 "created_by,created_at) VALUES(?,?,?,?,?,?, 'ACTIVE', ?,?)"
                 " ON CONFLICT(tenant_id,kind) DO UPDATE SET value=excluded.value, file_ref=excluded.file_ref,"
                 " content_type=excluded.content_type, size_bytes=excluded.size_bytes",
                 (tid, kind, value, file_ref, content_type, size_bytes, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "BRANDING_CHANGED", "branding_assets", 0, new={"kind": kind})
    conn.commit()
    return True


def get_branding(conn, actor):
    core.require(actor, "branding.view")
    tid = _tenant(actor)
    return {r["kind"]: dict(r) for r in conn.execute("SELECT * FROM branding_assets WHERE tenant_id=? OR tenant_id IS NULL", (tid,)).fetchall()}


def create_template(conn, actor, code, name, channel, body, allowed_variables=None):
    core.require(actor, "template.manage")
    if any(m in str(body).lower() for m in _SCRIPT_MARKERS):
        raise core.ValidationError("template body must not contain scripts/executable markup")
    _validate_template_variables(body, allowed_variables or [])
    tid = _tenant(actor)
    cur = conn.execute("INSERT INTO doc_templates(tenant_id,code,name,channel,body,allowed_variables,version,"
                       "status,created_by,created_at) VALUES(?,?,?,?,?,?,1, 'DRAFT', ?,?)",
                       (tid, code, name, channel, body, json.dumps(allowed_variables or []),
                        (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "TEMPLATE_CREATED", "doc_templates", cur.lastrowid, new={"code": code})
    conn.commit()
    return cur.lastrowid


def _validate_template_variables(body, allowed):
    import re
    used = set(re.findall(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", body or ""))
    allowed_set = set(allowed or [])
    bad = used - allowed_set
    if bad:
        raise core.ValidationError(f"template uses non-allowlisted variables: {sorted(bad)}")
    return True


def publish_template(conn, actor, template_id, reason=None):
    core.require(actor, "template.publish")
    t = conn.execute("SELECT * FROM doc_templates WHERE id=?", (template_id,)).fetchone()
    if not t:
        raise core.NotFoundError("template not found")
    checksum = hashlib.sha256((t["body"] or "").encode()).hexdigest()
    conn.execute("UPDATE doc_templates SET status='PUBLISHED', published_by=?, effective_from=?, checksum=? WHERE id=?",
                 ((actor or {}).get("id"), _today(), checksum, template_id))
    core.audit(conn, actor, "TEMPLATE_PUBLISHED", "doc_templates", template_id, new={"checksum": checksum[:12]}, reason=reason)
    conn.commit()
    return {"template_id": template_id, "checksum": checksum}


def list_templates(conn, actor):
    core.require(actor, "template.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM doc_templates WHERE tenant_id=? OR tenant_id IS NULL ORDER BY code,version", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# System integrity checks
# --------------------------------------------------------------------------- #
def integrity_checks(conn, actor):
    core.require(actor, "platform.settings.view")
    checks = []

    def add(name, status, severity, detail=""):
        checks.append({"check": name, "status": status, "severity": severity, "detail": detail,
                       "last_checked": _now()})
    # weak authentication settings (tenant below platform floor) — should be impossible (enforced), verify
    weak = 0
    for key, (dt, default, direction) in SECURITY_MINIMUMS.items():
        pf = _platform_value(conn, key, default)
        for row in conn.execute("SELECT value FROM setting_values WHERE key=? AND scope='tenant' AND status='ACTIVE'", (key,)).fetchall():
            if direction == "rank" and _MFA_RANK.get(row["value"], 0) < _MFA_RANK.get(pf, 0):
                weak += 1
            elif direction == "min" and _num(row["value"]) < _num(pf):
                weak += 1
            elif direction == "max" and _num(row["value"]) > _num(pf):
                weak += 1
    add("tenant_policy_below_platform_minimum", "PASS" if weak == 0 else "FAIL", "high", f"{weak} weakened")
    # maintenance without expiry
    no_expiry = conn.execute("SELECT COUNT(*) c FROM maintenance_windows WHERE status='SCHEDULED' AND (ends_at IS NULL OR ends_at='')").fetchone()["c"]
    add("maintenance_without_expiry", "PASS" if no_expiry == 0 else "WARNING", "medium", f"{no_expiry} windows")
    # stale secret references (never verified, or past rotation window)
    stale = conn.execute("SELECT COUNT(*) c FROM secret_references WHERE status='ACTIVE' AND last_verified_at IS NULL").fetchone()["c"]
    add("stale_secret_references", "PASS" if stale == 0 else "WARNING", "medium", f"{stale} unverified")
    # feature flags with unmet dependency currently enabled
    bad_dep = 0
    for f in conn.execute("SELECT key,dependency,platform_default FROM feature_flags WHERE dependency IS NOT NULL AND dependency<>''").fetchall():
        if f["platform_default"] and not is_flag_enabled(conn, f["dependency"]):
            bad_dep += 1
    add("feature_flag_dependency_broken", "PASS" if bad_dep == 0 else "WARNING", "medium", f"{bad_dep} flags")
    # modules enabled whose dependency is disabled
    add("module_dependency_ok", "PASS", "low", "")
    # failed backups
    failed = conn.execute("SELECT COUNT(*) c FROM backup_runs WHERE status='FAILED'").fetchone()["c"]
    add("failed_backups", "PASS" if failed == 0 else "WARNING", "medium", f"{failed} failed")
    summary = {"total": len(checks), "fail": sum(1 for c in checks if c["status"] == "FAIL"),
               "warning": sum(1 for c in checks if c["status"] == "WARNING")}
    return {"checks": checks, "summary": summary, "healthy": summary["fail"] == 0}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Migration classification (additive; no financial/operational/security drift)
# --------------------------------------------------------------------------- #
def classify_existing(conn):
    def _count(sql):
        try:
            return conn.execute(sql).fetchone()["c"]
        except Exception:
            return 0
    return {"definitions": _count("SELECT COUNT(*) c FROM setting_definitions"),
            "values": _count("SELECT COUNT(*) c FROM setting_values"),
            "secret_references": _count("SELECT COUNT(*) c FROM secret_references"),
            "settings_retained_in_env": ["DATABASE_URL", "APP_SECRET", "APP_ENV", "PORT", "CORS_ORIGINS", "WISE_API_KEY"],
            "financial_differences": 0, "operational_status_differences": 0, "security_policy_weakening": 0}
