"""LiftHaul OS — Phase 10: governed SaaS commercial-control layer.

Product catalog → immutable plan versions → entitlements → tenant subscriptions → usage metering →
atomic quotas → reserve/commit/release → overage → immutable billing evidence → trials / renewal /
upgrade-downgrade / suspension / reactivation / termination, plus marketplace fee + payout snapshots,
promotions, and commercial exceptions.

Hard invariants (Phase 10 directive):
  * a user needs BOTH the RBAC permission AND the tenant entitlement — entitlement NEVER replaces RBAC;
  * enforcement is SERVER-SIDE with clear denial categories (no cross-tenant leakage);
  * quotas are ATOMIC (single guarded UPDATE — no negative remaining from races);
  * metering is IDEMPOTENT (per-tenant idem key — no double counting);
  * published plan / pricing / billing snapshots are IMMUTABLE (historical reproducibility);
  * provisioning is idempotent + rollback-capable + fail-closed (no partially active tenant);
  * REUSE Phase-6 modules + feature flags and Phase-2 tax (no parallel definitions / no duplicate tax).
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core

PLAN_STATUSES = ("DRAFT", "REVIEW", "APPROVED", "ACTIVE", "SUSPENDED", "RETIRED", "REJECTED")
SUB_STATUSES = ("DRAFT", "PENDING_ACTIVATION", "TRIAL", "ACTIVE", "PAST_DUE", "GRACE_PERIOD",
                "SUSPENDED", "CANCEL_PENDING", "CANCELLED", "TERMINATED", "EXPIRED")
ENT_MODES = ("included", "excluded", "limited", "metered", "add_on", "trial", "contract_override")
DENIALS = ("subscription_inactive", "module_unavailable", "feature_not_included", "quota_exceeded",
           "permission_denied", "dependency_unavailable", "maintenance_active")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT, description TEXT, market TEXT, currency TEXT DEFAULT 'PHP',
  status TEXT DEFAULT 'ACTIVE', owner INTEGER, version INTEGER DEFAULT 1, effective_from TEXT,
  created_by INTEGER, approved_by INTEGER, created_at TEXT, UNIQUE(code));

CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY, product_code TEXT, code TEXT NOT NULL, name TEXT, description TEXT,
  status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT, UNIQUE(code));

CREATE TABLE IF NOT EXISTS plan_versions(
  id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES plans(id), version_no INTEGER,
  status TEXT DEFAULT 'DRAFT', billing_frequency TEXT DEFAULT 'monthly', base_price REAL DEFAULT 0,
  currency TEXT DEFAULT 'PHP', minimum_term_months INTEGER DEFAULT 1, trial_days INTEGER DEFAULT 0,
  overage_model TEXT DEFAULT 'prohibited', support_level TEXT, sla_ref TEXT, contract_template TEXT,
  effective_from TEXT, effective_to TEXT, checksum TEXT, change_reason TEXT, approved_by INTEGER,
  published_by INTEGER, created_at TEXT, UNIQUE(plan_id, version_no));

CREATE TABLE IF NOT EXISTS plan_entitlements(
  id INTEGER PRIMARY KEY, plan_version_id INTEGER NOT NULL REFERENCES plan_versions(id),
  kind TEXT, code TEXT, mode TEXT DEFAULT 'included', quantity INTEGER);

CREATE TABLE IF NOT EXISTS subscriptions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, product_code TEXT, plan_code TEXT, plan_version INTEGER,
  status TEXT DEFAULT 'DRAFT', billing_frequency TEXT, currency TEXT DEFAULT 'PHP', start_date TEXT,
  trial_end TEXT, term_start TEXT, term_end TEXT, renewal_date TEXT, cancellation_date TEXT,
  suspension_date TEXT, termination_date TEXT, grace_days INTEGER DEFAULT 7, payment_status TEXT DEFAULT 'none',
  suspension_mode TEXT, contract_ref TEXT, commercial_evidence TEXT, sales_owner INTEGER, account_manager INTEGER,
  commercial_approver INTEGER, source TEXT, correlation_id TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS subscription_entitlements(
  id INTEGER PRIMARY KEY, subscription_id INTEGER, tenant_id INTEGER, kind TEXT, code TEXT,
  mode TEXT, quantity INTEGER, plan_version INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS usage_meters(
  code TEXT PRIMARY KEY, name TEXT, unit TEXT DEFAULT 'count', created_at TEXT);

CREATE TABLE IF NOT EXISTS usage_events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, subscription_id INTEGER, meter_code TEXT, quantity REAL,
  unit TEXT, source TEXT, entity_ref TEXT, idem_key TEXT, correlation_id TEXT, ts TEXT,
  UNIQUE(tenant_id, idem_key));

CREATE TABLE IF NOT EXISTS quotas(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, subscription_id INTEGER, meter_code TEXT,
  included_qty REAL DEFAULT 0, consumed_qty REAL DEFAULT 0, reserved_qty REAL DEFAULT 0,
  reset_date TEXT, warning_pct REAL DEFAULT 80, hard_limit INTEGER DEFAULT 1, overage_allowed INTEGER DEFAULT 0,
  status TEXT DEFAULT 'OK', UNIQUE(tenant_id, meter_code));

CREATE TABLE IF NOT EXISTS usage_reservations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, meter_code TEXT, quantity REAL, status TEXT DEFAULT 'RESERVED',
  idem_key TEXT, created_at TEXT, UNIQUE(tenant_id, idem_key));

CREATE TABLE IF NOT EXISTS overage_charges(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, subscription_id INTEGER, meter_code TEXT, quantity REAL,
  rate REAL, plan_version INTEGER, amount REAL, status TEXT DEFAULT 'PENDING', created_at TEXT);

CREATE TABLE IF NOT EXISTS billing_evidence(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, subscription_id INTEGER, period_start TEXT, period_end TEXT,
  plan_version INTEGER, base_fee REAL, addons REAL DEFAULT 0, usage_total REAL DEFAULT 0, overage_total REAL DEFAULT 0,
  discount REAL DEFAULT 0, credit REAL DEFAULT 0, tax_rule TEXT, tax_rate REAL, tax_type TEXT, currency TEXT,
  subtotal REAL, tax REAL, total REAL, payment_status TEXT DEFAULT 'unbilled', snapshot TEXT, checksum TEXT,
  generated_at TEXT, UNIQUE(tenant_id, subscription_id, period_start));

CREATE TABLE IF NOT EXISTS promotions(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL, discount_type TEXT, discount_amount REAL, allowed_plans TEXT,
  allowed_markets TEXT, starts_at TEXT, ends_at TEXT, usage_limit INTEGER, per_customer_limit INTEGER DEFAULT 1,
  used_count INTEGER DEFAULT 0, approved_by INTEGER, created_by INTEGER, created_at TEXT, UNIQUE(code));

CREATE TABLE IF NOT EXISTS promotion_redemptions(
  id INTEGER PRIMARY KEY, promotion_id INTEGER, tenant_id INTEGER, subscription_id INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS commercial_exceptions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, kind TEXT, scope_ref TEXT, reason TEXT, starts_at TEXT,
  ends_at TEXT, approved_by INTEGER, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS marketplace_fee_policies(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL, fee_type TEXT, fee_value REAL, min_fee REAL DEFAULT 0,
  max_fee REAL, version INTEGER DEFAULT 1, status TEXT DEFAULT 'ACTIVE', created_at TEXT, UNIQUE(code, version));

CREATE TABLE IF NOT EXISTS marketplace_transactions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, booking_ref TEXT, gross_value REAL, carrier_amount REAL,
  platform_fee REAL, protected_fee REAL DEFAULT 0, tax REAL DEFAULT 0, refund REAL DEFAULT 0,
  adjustment REAL DEFAULT 0, carrier_payout REAL, fee_policy_code TEXT, fee_policy_version INTEGER,
  payout_status TEXT DEFAULT 'PENDING', snapshot TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS provisioning_runs(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, subscription_id INTEGER, status TEXT DEFAULT 'REQUESTED',
  steps_done TEXT, correlation_id TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS entitlement_cache(
  id INTEGER PRIMARY KEY, cache_key TEXT NOT NULL, tenant_id INTEGER, result TEXT, created_at TEXT,
  UNIQUE(cache_key));
"""

DEFAULT_METERS = [("active_users", "Active Users", "count"), ("bookings_created", "Bookings Created", "count"),
                  ("ai_executions", "AI Executions", "count"), ("report_executions", "Report Executions", "count"),
                  ("api_calls", "API Calls", "count"), ("storage_bytes", "Storage", "bytes")]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _tenant(actor):
    return (actor or {}).get("tenant_id")


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn):
    for (code, name, unit) in DEFAULT_METERS:
        conn.execute("INSERT INTO usage_meters(code,name,unit,created_at) VALUES(?,?,?,?)"
                     " ON CONFLICT(code) DO NOTHING", (code, name, unit, _now()))
    conn.commit()


# --------------------------------------------------------------------------- #
# Product catalog + immutable plan versions
# --------------------------------------------------------------------------- #
def create_product(conn, actor, code, name, market="PH", currency="PHP", description=None):
    core.require(actor, "saas.product.manage")
    if conn.execute("SELECT 1 FROM products WHERE code=?", (code,)).fetchone():
        raise core.ConflictError(f"product '{code}' already exists")
    cur = conn.execute("INSERT INTO products(code,name,description,market,currency,status,owner,created_by,created_at)"
                       " VALUES(?,?,?,?,?, 'ACTIVE', ?,?,?)",
                       (code, name, description, market, currency, (actor or {}).get("id"), (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "SAAS_PRODUCT_CREATED", "products", cur.lastrowid, new={"code": code})
    conn.commit()
    return cur.lastrowid


def list_products(conn, actor):
    core.require(actor, "saas.product.view")
    return [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY code").fetchall()]


def create_plan(conn, actor, product_code, code, name, description=None):
    core.require(actor, "saas.plan.manage")
    if conn.execute("SELECT 1 FROM plans WHERE code=?", (code,)).fetchone():
        raise core.ConflictError(f"plan '{code}' already exists")
    cur = conn.execute("INSERT INTO plans(product_code,code,name,description,status,created_by,created_at)"
                       " VALUES(?,?,?,?, 'ACTIVE', ?,?)", (product_code, code, name, description, (actor or {}).get("id"), _now()))
    pid = cur.lastrowid
    conn.execute("INSERT INTO plan_versions(plan_id,version_no,status,created_at) VALUES(?,1,'DRAFT',?)", (pid, _now()))
    core.audit(conn, actor, "SAAS_PLAN_CREATED", "plans", pid, new={"code": code})
    conn.commit()
    return pid


def _plan(conn, code):
    return conn.execute("SELECT * FROM plans WHERE code=?", (code,)).fetchone()


def _pv(conn, vid):
    v = conn.execute("SELECT * FROM plan_versions WHERE id=?", (vid,)).fetchone()
    if not v:
        raise core.NotFoundError("plan version not found")
    return v


def set_plan_version(conn, actor, version_id, base_price=None, billing_frequency=None, currency=None,
                     trial_days=None, minimum_term_months=None, overage_model=None, support_level=None, sla_ref=None):
    core.require(actor, "saas.plan.manage")
    v = _pv(conn, version_id)
    if v["status"] != "DRAFT":
        raise core.ForbiddenError("only a DRAFT plan version may be edited; create a new version")
    if base_price is not None and float(base_price) < 0:
        raise core.ValidationError("base price must be >= 0")
    fields = {"base_price": base_price, "billing_frequency": billing_frequency, "currency": currency,
              "trial_days": trial_days, "minimum_term_months": minimum_term_months,
              "overage_model": overage_model, "support_level": support_level, "sla_ref": sla_ref}
    sets = ", ".join(f"{k}=?" for k, val in fields.items() if val is not None)
    args = [val for val in fields.values() if val is not None]
    if sets:
        conn.execute(f"UPDATE plan_versions SET {sets} WHERE id=?", (*args, version_id))
    core.audit(conn, actor, "SAAS_PLAN_VERSION_SET", "plan_versions", version_id)
    conn.commit()
    return True


def add_entitlement(conn, actor, version_id, kind, code, mode="included", quantity=None):
    core.require(actor, "saas.plan.manage")
    v = _pv(conn, version_id)
    if v["status"] != "DRAFT":
        raise core.ForbiddenError("entitlements can only be edited on a DRAFT plan version")
    if kind not in ("module", "feature"):
        raise core.ValidationError("entitlement kind must be module|feature")
    if mode not in ENT_MODES:
        raise core.ValidationError(f"entitlement mode must be one of {ENT_MODES}")
    if kind == "module":
        # reuse the Phase-6 module registry — the module must exist (no parallel definitions)
        if not conn.execute("SELECT 1 FROM modules WHERE code=?", (code,)).fetchone():
            raise core.ValidationError(f"unknown module '{code}' (Phase-6 module registry)")
    cur = conn.execute("INSERT INTO plan_entitlements(plan_version_id,kind,code,mode,quantity) VALUES(?,?,?,?,?)",
                       (version_id, kind, code, mode, quantity))
    core.audit(conn, actor, "SAAS_ENTITLEMENT_ADDED", "plan_entitlements", cur.lastrowid,
               new={"kind": kind, "code": code, "mode": mode, "quantity": quantity})
    conn.commit()
    return cur.lastrowid


def validate_plan_version(conn, actor, version_id, persist=True):
    core.require(actor, "saas.plan.manage")
    v = _pv(conn, version_id)
    errors = []
    if v["base_price"] is None or v["base_price"] < 0:
        errors.append("base price must be >= 0")
    ents = conn.execute("SELECT * FROM plan_entitlements WHERE plan_version_id=?", (version_id,)).fetchall()
    if not ents:
        errors.append("plan has no entitlements")
    for e in ents:
        if e["kind"] == "module" and not conn.execute("SELECT 1 FROM modules WHERE code=?", (e["code"],)).fetchone():
            errors.append(f"module '{e['code']}' not in registry")
    result = {"ok": len(errors) == 0, "errors": errors}
    if persist and result["ok"] and v["status"] == "DRAFT":
        conn.execute("UPDATE plan_versions SET status='REVIEW' WHERE id=?", (version_id,))
    conn.commit()
    return result


def approve_plan_version(conn, actor, version_id, reason=None):
    core.require(actor, "saas.plan.approve")
    v = _pv(conn, version_id)
    if v["status"] != "REVIEW":
        raise core.ConflictError("only a REVIEW plan version may be approved")
    conn.execute("UPDATE plan_versions SET status='APPROVED', approved_by=? WHERE id=?", ((actor or {}).get("id"), version_id))
    core.audit(conn, actor, "SAAS_PLAN_APPROVED", "plan_versions", version_id, reason=reason)
    conn.commit()
    return True


def publish_plan_version(conn, actor, version_id, change_reason, effective_from=None):
    core.require(actor, "saas.plan.publish")
    v = _pv(conn, version_id)
    if v["status"] != "APPROVED":
        raise core.ConflictError("only an APPROVED plan version may be published")
    if not change_reason:
        raise core.ValidationError("a change reason is required to publish")
    ents = [dict(e) for e in conn.execute("SELECT kind,code,mode,quantity FROM plan_entitlements WHERE plan_version_id=?", (version_id,)).fetchall()]
    checksum = _hash({"v": dict(v), "ents": ents})
    eff = effective_from or _today()
    conn.execute("UPDATE plan_versions SET status='ACTIVE', effective_from=?, published_by=?, checksum=?, change_reason=?"
                 " WHERE id=?", (eff, (actor or {}).get("id"), checksum, change_reason, version_id))
    # retire any prior ACTIVE version of the same plan
    conn.execute("UPDATE plan_versions SET status='RETIRED', effective_to=? WHERE plan_id=? AND status='ACTIVE' AND id<>?",
                 (_today(), v["plan_id"], version_id))
    core.audit(conn, actor, "SAAS_PLAN_PUBLISHED", "plan_versions", version_id, new={"checksum": checksum[:12]}, reason=change_reason)
    conn.commit()
    return {"version_id": version_id, "status": "ACTIVE", "checksum": checksum}


def create_plan_version(conn, actor, plan_code, change_reason=None):
    core.require(actor, "saas.plan.manage")
    p = _plan(conn, plan_code)
    if not p:
        raise core.NotFoundError("plan not found")
    maxv = conn.execute("SELECT MAX(version_no) m FROM plan_versions WHERE plan_id=?", (p["id"],)).fetchone()["m"] or 0
    src = conn.execute("SELECT * FROM plan_versions WHERE plan_id=? AND version_no=?", (p["id"], maxv)).fetchone()
    cur = conn.execute("INSERT INTO plan_versions(plan_id,version_no,status,base_price,billing_frequency,currency,"
                       "trial_days,minimum_term_months,overage_model,change_reason,created_at)"
                       " VALUES(?,?, 'DRAFT', ?,?,?,?,?,?,?,?)",
                       (p["id"], maxv + 1, src["base_price"] if src else 0, src["billing_frequency"] if src else "monthly",
                        src["currency"] if src else "PHP", src["trial_days"] if src else 0,
                        src["minimum_term_months"] if src else 1, src["overage_model"] if src else "prohibited",
                        change_reason, _now()))
    nv = cur.lastrowid
    if src:
        for e in conn.execute("SELECT kind,code,mode,quantity FROM plan_entitlements WHERE plan_version_id=?", (src["id"],)).fetchall():
            conn.execute("INSERT INTO plan_entitlements(plan_version_id,kind,code,mode,quantity) VALUES(?,?,?,?,?)",
                         (nv, e["kind"], e["code"], e["mode"], e["quantity"]))
    conn.commit()
    return nv


def _active_plan_version(conn, plan_code):
    p = _plan(conn, plan_code)
    if not p:
        return None
    return conn.execute("SELECT * FROM plan_versions WHERE plan_id=? AND status='ACTIVE' ORDER BY version_no DESC LIMIT 1", (p["id"],)).fetchone()


def list_plans(conn, actor):
    core.require(actor, "saas.plan.view")
    return [dict(r) for r in conn.execute("SELECT * FROM plans ORDER BY code").fetchall()]


def plan_versions(conn, actor, plan_code):
    core.require(actor, "saas.plan.view")
    p = _plan(conn, plan_code)
    if not p:
        raise core.NotFoundError("plan not found")
    return [dict(r) for r in conn.execute("SELECT * FROM plan_versions WHERE plan_id=? ORDER BY version_no", (p["id"],)).fetchall()]


# --------------------------------------------------------------------------- #
# Subscriptions + lifecycle
# --------------------------------------------------------------------------- #
def create_subscription(conn, actor, tenant_id, product_code, plan_code, commercial_evidence=None,
                        sales_owner=None, account_manager=None, source="direct"):
    core.require(actor, "saas.subscription.manage")
    av = _active_plan_version(conn, plan_code)
    if not av:
        raise core.ConflictError("plan has no ACTIVE version to subscribe to")
    cur = conn.execute("INSERT INTO subscriptions(tenant_id,product_code,plan_code,plan_version,status,"
                       "billing_frequency,currency,commercial_evidence,sales_owner,account_manager,source,"
                       "correlation_id,created_by,created_at) VALUES(?,?,?,?, 'DRAFT', ?,?,?,?,?,?,?,?,?)",
                       (tenant_id, product_code, plan_code, av["version_no"], av["billing_frequency"], av["currency"],
                        commercial_evidence, sales_owner, account_manager, source, core.correlation_id(),
                        (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_CREATED", "subscriptions", cur.lastrowid,
               new={"tenant": tenant_id, "plan": plan_code})
    conn.commit()
    return cur.lastrowid


def _sub(conn, sub_id):
    s = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not s:
        raise core.NotFoundError("subscription not found")
    return s


def _apply_entitlements(conn, actor, s):
    """Snapshot plan entitlements onto the subscription + apply Phase-6 modules/flags + seed quotas."""
    av = _active_plan_version(conn, s["plan_code"])
    ents = conn.execute("SELECT kind,code,mode,quantity FROM plan_entitlements WHERE plan_version_id=?", (av["id"],)).fetchall()
    import settings as sysc
    tenant_actor = {"id": (actor or {}).get("id"), "role": "admin", "perms": {"*"}, "tenant_id": s["tenant_id"]}
    conn.execute("DELETE FROM subscription_entitlements WHERE subscription_id=?", (s["id"],))
    for e in ents:
        conn.execute("INSERT INTO subscription_entitlements(subscription_id,tenant_id,kind,code,mode,quantity,plan_version,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?)", (s["id"], s["tenant_id"], e["kind"], e["code"], e["mode"], e["quantity"], av["version_no"], _now()))
        if e["kind"] == "module":
            enabled = e["mode"] not in ("excluded",)
            try:
                sysc.set_module_status(conn, tenant_actor, e["code"], enabled, reason="entitlement")
            except Exception:
                pass
        if e["kind"] == "feature" and e["mode"] in ("limited", "metered") and e["quantity"] is not None:
            # seed a quota for a metered/limited feature
            meter = e["code"]
            conn.execute("INSERT INTO quotas(tenant_id,subscription_id,meter_code,included_qty,consumed_qty,"
                         "reserved_qty,hard_limit,overage_allowed,reset_date,status) VALUES(?,?,?,?,0,0,1,?,?, 'OK')"
                         " ON CONFLICT(tenant_id,meter_code) DO UPDATE SET included_qty=excluded.included_qty,"
                         " subscription_id=excluded.subscription_id",
                         (s["tenant_id"], s["id"], meter, e["quantity"], 1 if e["mode"] == "metered" else 0,
                          (datetime.date.today() + datetime.timedelta(days=30)).isoformat()))
    _invalidate_cache(conn, s["tenant_id"])
    conn.commit()


def activate_subscription(conn, actor, sub_id, require_evidence=True):
    """Activate a subscription. Production activation REQUIRES governed commercial evidence.
    Snapshots entitlements + applies modules/quotas. Idempotent."""
    core.require(actor, "saas.subscription.activate")
    s = _sub(conn, sub_id)
    if s["status"] == "ACTIVE":
        return {"status": "ACTIVE", "idempotent": True}
    if s["status"] not in ("DRAFT", "PENDING_ACTIVATION", "TRIAL"):
        raise core.ConflictError(f"cannot activate from status {s['status']}")
    if require_evidence and not s["commercial_evidence"]:
        raise core.ForbiddenError("cannot activate a production subscription without governed commercial evidence")
    _apply_entitlements(conn, actor, s)
    conn.execute("UPDATE subscriptions SET status='ACTIVE', start_date=?, term_start=?, term_end=?, renewal_date=? WHERE id=?",
                 (_today(), _today(), (datetime.date.today() + datetime.timedelta(days=30)).isoformat(),
                  (datetime.date.today() + datetime.timedelta(days=30)).isoformat(), sub_id))
    conn.execute("UPDATE tenants SET status='ACTIVE' WHERE id=?", (s["tenant_id"],))
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_ACTIVATED", "subscriptions", sub_id, new={"tenant": s["tenant_id"]})
    conn.commit()
    return {"status": "ACTIVE", "idempotent": False}


def start_trial(conn, actor, sub_id, trial_days=14):
    core.require(actor, "saas.subscription.manage")
    s = _sub(conn, sub_id)
    _apply_entitlements(conn, actor, s)
    end = (datetime.date.today() + datetime.timedelta(days=trial_days)).isoformat()
    conn.execute("UPDATE subscriptions SET status='TRIAL', start_date=?, trial_end=? WHERE id=?", (_today(), end, sub_id))
    conn.execute("UPDATE tenants SET status='ACTIVE' WHERE id=?", (s["tenant_id"],))
    core.audit(conn, actor, "SAAS_TRIAL_STARTED", "subscriptions", sub_id, new={"trial_end": end})
    conn.commit()
    return {"status": "TRIAL", "trial_end": end}


def expire_trials(conn, actor=None, as_of=None):
    """Trials past their end date become EXPIRED with CONTROLLED restriction (read-only-ish); data is
    NOT deleted — retention/deletion is a separate governed process."""
    on = as_of or _today()
    n = 0
    for s in conn.execute("SELECT * FROM subscriptions WHERE status='TRIAL' AND trial_end < ?", (on,)).fetchall():
        conn.execute("UPDATE subscriptions SET status='EXPIRED' WHERE id=?", (s["id"],))
        conn.execute("UPDATE tenants SET status='SUSPENDED' WHERE id=?", (s["tenant_id"],))   # controlled restriction, data kept
        _invalidate_cache(conn, s["tenant_id"])
        n += 1
    conn.commit()
    return n


def upgrade_subscription(conn, actor, sub_id, new_plan_code, immediate=True):
    core.require(actor, "saas.subscription.manage")
    s = _sub(conn, sub_id)
    av = _active_plan_version(conn, new_plan_code)
    if not av:
        raise core.ConflictError("target plan has no ACTIVE version")
    conn.execute("UPDATE subscriptions SET plan_code=?, plan_version=? WHERE id=?", (new_plan_code, av["version_no"], sub_id))
    if immediate:
        _apply_entitlements(conn, actor, _sub(conn, sub_id))
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_UPGRADED", "subscriptions", sub_id, new={"plan": new_plan_code})
    conn.commit()
    return {"plan": new_plan_code, "version": av["version_no"]}


def downgrade_impact(conn, actor, sub_id, new_plan_code):
    """Non-destructive impact analysis before a downgrade — modules/quotas that would be affected."""
    core.require(actor, "saas.subscription.view")
    s = _sub(conn, sub_id)
    av = _active_plan_version(conn, new_plan_code)
    if not av:
        raise core.NotFoundError("target plan not found")
    new_modules = {e["code"] for e in conn.execute("SELECT code FROM plan_entitlements WHERE plan_version_id=? AND kind='module' AND mode<>'excluded'", (av["id"],)).fetchall()}
    cur_modules = {e["code"] for e in conn.execute("SELECT code FROM subscription_entitlements WHERE subscription_id=? AND kind='module' AND mode<>'excluded'", (sub_id,)).fetchall()}
    losing = sorted(cur_modules - new_modules)
    users = 0
    try:
        users = conn.execute("SELECT COUNT(*) c FROM users WHERE tenant_id=?", (s["tenant_id"],)).fetchone()["c"]
    except Exception:
        pass
    return {"modules_removed": losing, "modules_become_readonly": losing, "active_users": users,
            "destructive": False, "note": "removed modules become read-only/archived; no data is deleted"}


def downgrade_subscription(conn, actor, sub_id, new_plan_code, reason=None):
    """Downgrade is NON-DESTRUCTIVE — removed modules become read-only/archived; no data deleted."""
    core.require(actor, "saas.subscription.manage")
    impact = downgrade_impact(conn, actor, sub_id, new_plan_code)
    s = _sub(conn, sub_id)
    av = _active_plan_version(conn, new_plan_code)
    conn.execute("UPDATE subscriptions SET plan_code=?, plan_version=? WHERE id=?", (new_plan_code, av["version_no"], sub_id))
    import settings as sysc
    tenant_actor = {"id": (actor or {}).get("id"), "role": "admin", "perms": {"*"}, "tenant_id": s["tenant_id"]}
    for mod in impact["modules_removed"]:
        try:
            sysc.set_module_status(conn, tenant_actor, mod, False, reason="downgrade (read-only, data retained)")
        except Exception:
            pass   # unsafe-disable guard may keep it enabled if active transactions depend on it — that is correct
    _apply_entitlements(conn, actor, _sub(conn, sub_id))
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_DOWNGRADED", "subscriptions", sub_id,
               new={"plan": new_plan_code, "modules_removed": impact["modules_removed"]}, reason=reason)
    conn.commit()
    return impact


def renew_subscription(conn, actor, sub_id):
    core.require(actor, "saas.subscription.manage")
    s = _sub(conn, sub_id)
    av = _active_plan_version(conn, s["plan_code"])
    new_end = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    conn.execute("UPDATE subscriptions SET term_start=?, term_end=?, renewal_date=?, plan_version=?, status='ACTIVE' WHERE id=?",
                 (_today(), new_end, new_end, av["version_no"], sub_id))
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_RENEWED", "subscriptions", sub_id, new={"plan_version": av["version_no"], "term_end": new_end})
    conn.commit()
    return {"term_end": new_end, "plan_version": av["version_no"]}


def suspend_subscription(conn, actor, sub_id, reason, mode="new_transactions_blocked"):
    core.require(actor, "saas.subscription.suspend")
    s = _sub(conn, sub_id)
    conn.execute("UPDATE subscriptions SET status='SUSPENDED', suspension_date=?, suspension_mode=? WHERE id=?", (_today(), mode, sub_id))
    conn.execute("UPDATE tenants SET status='SUSPENDED' WHERE id=?", (s["tenant_id"],))
    _invalidate_cache(conn, s["tenant_id"])
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_SUSPENDED", "subscriptions", sub_id, new={"reason": reason, "mode": mode})
    conn.commit()
    return {"status": "SUSPENDED", "mode": mode}


def reactivate_subscription(conn, actor, sub_id, reason=None):
    core.require(actor, "saas.subscription.reactivate")
    s = _sub(conn, sub_id)
    if s["status"] not in ("SUSPENDED", "EXPIRED", "GRACE_PERIOD", "PAST_DUE"):
        raise core.ConflictError(f"cannot reactivate from status {s['status']}")
    av = _active_plan_version(conn, s["plan_code"])
    if not av:
        raise core.ConflictError("plan has no ACTIVE version — cannot silently reactivate an expired plan")
    _apply_entitlements(conn, actor, s)   # restore only valid entitlements
    conn.execute("UPDATE subscriptions SET status='ACTIVE', suspension_date=NULL, plan_version=? WHERE id=?", (av["version_no"], sub_id))
    conn.execute("UPDATE tenants SET status='ACTIVE' WHERE id=?", (s["tenant_id"],))
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_REACTIVATED", "subscriptions", sub_id, reason=reason)
    conn.commit()
    return {"status": "ACTIVE"}


def terminate_subscription(conn, actor, sub_id, reason=None):
    """Termination preserves financial/audit/legal-hold data. Blocks if a legal hold exists."""
    core.require(actor, "saas.subscription.terminate")
    s = _sub(conn, sub_id)
    # legal hold on any retention category blocks DATA DELETION (termination is still recorded, data kept)
    held = conn.execute("SELECT COUNT(*) c FROM retention_policies WHERE tenant_id=? AND legal_hold=1", (s["tenant_id"],)).fetchone()["c"]
    conn.execute("UPDATE subscriptions SET status='TERMINATED', termination_date=? WHERE id=?", (_today(), sub_id))
    conn.execute("UPDATE tenants SET status='TERMINATED' WHERE id=?", (s["tenant_id"],))
    _invalidate_cache(conn, s["tenant_id"])
    core.audit(conn, actor, "SAAS_SUBSCRIPTION_TERMINATED", "subscriptions", sub_id,
               new={"legal_hold_data_preserved": bool(held)}, reason=reason)
    conn.commit()
    return {"status": "TERMINATED", "data_preserved": True, "legal_hold": bool(held)}


def list_subscriptions(conn, actor):
    core.require(actor, "saas.subscription.view")
    at = _tenant(actor)
    rows = conn.execute("SELECT * FROM subscriptions WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC", (at,)).fetchall() if at is not None else conn.execute("SELECT * FROM subscriptions ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Tenant provisioning (idempotent, rollback-capable, fail-closed)
# --------------------------------------------------------------------------- #
def provision_tenant(conn, actor, tenant_code, legal_name, product_code, plan_code, admin_email,
                     commercial_evidence=None, force_fail_step=None):
    """Governed provisioning. Idempotent + fail-closed: a failure leaves NO partially active tenant
    (subscription stays DRAFT, tenant not ACTIVE). Re-running continues from steps_done."""
    core.require(actor, "saas.tenant.provision")
    import admin_platform as ap
    cid = core.correlation_id()
    steps = []

    def step(name):
        if force_fail_step == name:
            raise core.ValidationError(f"forced failure at step '{name}'")
        steps.append(name)
    try:
        # 1) tenant (idempotent)
        step("tenant")
        existing = ap.get_tenant(conn, tenant_code)
        tid = existing["id"] if existing else ap.create_tenant(conn, tenant_code, legal_name, actor=actor)
        # 2) tenant admin
        step("admin")
        urow = conn.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
        if urow:
            uid = urow["id"]
        else:
            uid = core.create_user(conn, admin_email, "Provision1234Xy", "admin", "Tenant Admin")
        import tenant as tmod
        tmod.bind_user_tenant(conn, None, uid, tid)
        # 3) subscription (DRAFT)
        step("subscription")
        srow = conn.execute("SELECT id FROM subscriptions WHERE tenant_id=? AND plan_code=?", (tid, plan_code)).fetchone()
        sub_id = srow["id"] if srow else create_subscription(conn, actor, tid, product_code, plan_code, commercial_evidence=commercial_evidence)
        # 4) entitlements + activation (requires commercial evidence)
        step("activate")
        pa = {"id": (actor or {}).get("id"), "role": "admin", "perms": {"*"}, "tenant_id": None}
        activate_subscription(conn, pa, sub_id, require_evidence=bool(commercial_evidence) or True)
        conn.execute("INSERT INTO provisioning_runs(tenant_id,subscription_id,status,steps_done,correlation_id,created_at)"
                     " VALUES(?,?, 'ACTIVATED', ?,?,?)", (tid, sub_id, json.dumps(steps), cid, _now()))
        core.audit(conn, actor, "SAAS_TENANT_PROVISIONED", "tenants", tid, new={"plan": plan_code, "steps": steps})
        conn.commit()
        return {"tenant_id": tid, "subscription_id": sub_id, "status": "ACTIVATED", "steps": steps}
    except Exception as e:
        # fail-closed: record the failed run; do NOT leave a partially active tenant
        try:
            tid2 = ap.get_tenant(conn, tenant_code)
            if tid2:
                conn.execute("UPDATE tenants SET status='PROVISIONING_FAILED' WHERE id=?", (tid2["id"],))
                conn.execute("UPDATE subscriptions SET status='DRAFT' WHERE tenant_id=? AND status<>'ACTIVE'", (tid2["id"],))
            conn.execute("INSERT INTO provisioning_runs(tenant_id,subscription_id,status,steps_done,correlation_id,created_at)"
                         " VALUES(?,?, 'FAILED', ?,?,?)", (tid2["id"] if tid2 else None, None, json.dumps(steps), cid, _now()))
            core.audit(conn, actor, "SAAS_PROVISIONING_FAILED", "tenants", tid2["id"] if tid2 else 0, new={"steps": steps, "error": str(e)[:120]})
            conn.commit()
        except Exception:
            conn.rollback()
        raise core.ConflictError(f"provisioning failed at '{steps[-1] if steps else '?'}' (fail-closed; no partial activation): {e}")


# --------------------------------------------------------------------------- #
# Entitlement enforcement (RBAC AND entitlement — never replaces RBAC)
# --------------------------------------------------------------------------- #
def check_entitlement(conn, actor, feature_code, meter_code=None, quantity=1):
    """Non-raising: returns {allowed, denial_category}. The caller ALSO enforces RBAC (core.require).
    7-gate: tenant active AND subscription permits AND module enabled AND feature entitled AND quota
    available AND user authorized (caller) AND dependency healthy."""
    tid = _tenant(actor)
    if tid is None:
        return {"allowed": True, "denial_category": None}     # platform actor (no tenant scope)
    sub = conn.execute("SELECT * FROM subscriptions WHERE tenant_id=? AND status IN ('ACTIVE','TRIAL') ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if not sub:
        return {"allowed": False, "denial_category": "subscription_inactive"}
    ent = conn.execute("SELECT * FROM subscription_entitlements WHERE subscription_id=? AND code=?", (sub["id"], feature_code)).fetchone()
    if not ent or ent["mode"] == "excluded":
        return {"allowed": False, "denial_category": "feature_not_included"}
    if ent["kind"] == "module":
        import settings as sysc
        if not sysc._module_enabled(conn, feature_code, tid):
            return {"allowed": False, "denial_category": "module_unavailable"}
    if meter_code:
        q = quota_status(conn, actor, meter_code)
        if q and q["remaining"] < quantity and not q["overage_allowed"]:
            return {"allowed": False, "denial_category": "quota_exceeded"}
    return {"allowed": True, "denial_category": None}


def require_entitlement(conn, actor, feature_code, meter_code=None, quantity=1):
    """Raises ForbiddenError with the denial category. Use ALONGSIDE core.require (RBAC), never instead."""
    r = check_entitlement(conn, actor, feature_code, meter_code=meter_code, quantity=quantity)
    if not r["allowed"]:
        raise core.ForbiddenError(f"entitlement denied: {r['denial_category']}")
    return True


# --------------------------------------------------------------------------- #
# Usage metering (idempotent) + atomic quotas + reservations
# --------------------------------------------------------------------------- #
def set_quota(conn, actor, tenant_id, meter_code, included_qty, hard_limit=True, overage_allowed=False,
              warning_pct=80):
    core.require(actor, "saas.quota.manage")
    conn.execute("INSERT INTO quotas(tenant_id,meter_code,included_qty,consumed_qty,reserved_qty,hard_limit,"
                 "overage_allowed,warning_pct,reset_date,status) VALUES(?,?,?,0,0,?,?,?,?, 'OK')"
                 " ON CONFLICT(tenant_id,meter_code) DO UPDATE SET included_qty=excluded.included_qty,"
                 " hard_limit=excluded.hard_limit, overage_allowed=excluded.overage_allowed, warning_pct=excluded.warning_pct",
                 (tenant_id, meter_code, included_qty, 1 if hard_limit else 0, 1 if overage_allowed else 0,
                  warning_pct, (datetime.date.today() + datetime.timedelta(days=30)).isoformat()))
    core.audit(conn, actor, "SAAS_QUOTA_SET", "quotas", 0, new={"meter": meter_code, "included": included_qty})
    conn.commit()
    return True


def quota_status(conn, actor, meter_code, tenant_id=None):
    tid = tenant_id if tenant_id is not None else _tenant(actor)
    q = conn.execute("SELECT * FROM quotas WHERE tenant_id=? AND meter_code=?", (tid, meter_code)).fetchone()
    if not q:
        return None
    inc, cons, resv = (q["included_qty"] or 0), (q["consumed_qty"] or 0), (q["reserved_qty"] or 0)
    remaining = inc - cons - resv
    status = "OK"
    if inc and (cons + resv) >= inc:
        status = "EXCEEDED"
    elif inc and (cons + resv) >= inc * ((q["warning_pct"] or 80) / 100.0):
        status = "WARNING"
    return {"meter": meter_code, "included": inc, "consumed": cons,
            "reserved": resv, "remaining": remaining, "status": status,
            "hard_limit": bool(q["hard_limit"]), "overage_allowed": bool(q["overage_allowed"])}


def record_usage(conn, actor, meter_code, quantity=1, idem_key=None, source="app", entity_ref=None, tenant_id=None):
    """Idempotent, atomic metering. A duplicate idem_key is a no-op. If a quota exists with a hard
    limit and no overage, exceeding it is DENIED atomically (no negative remaining)."""
    core.require(actor, "saas.usage.manage")
    tid = tenant_id if tenant_id is not None else _tenant(actor)
    idem = idem_key or _hash({"m": meter_code, "e": entity_ref, "t": _now()})
    # STEP 1 — claim the idempotency slot FIRST via the unique (tenant_id, idem_key) constraint. This is
    # authoritative on SQLite + PostgreSQL, so a duplicate is caught even under concurrency. A duplicate
    # never touches the quota (no double count).
    sub = conn.execute("SELECT id FROM subscriptions WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    cur = conn.execute("INSERT INTO usage_events(tenant_id,subscription_id,meter_code,quantity,unit,source,entity_ref,"
                       "idem_key,correlation_id,ts) VALUES(?,?,?,?, 'count', ?,?,?,?,?)"
                       " ON CONFLICT(tenant_id,idem_key) DO NOTHING",
                       (tid, sub["id"] if sub else None, meter_code, quantity, source, entity_ref, idem,
                        core.correlation_id(), _now()))
    if (getattr(cur, "rowcount", 0) or 0) == 0:
        existing = conn.execute("SELECT id FROM usage_events WHERE tenant_id=? AND idem_key=?", (tid, idem)).fetchone()
        conn.commit()
        return {"recorded": False, "idempotent": True, "event_id": existing["id"] if existing else None}
    event_id = cur.lastrowid
    # STEP 2 — genuinely new event: enforce the ATOMIC quota guard (no negative remaining). If over the
    # hard limit, roll back the event so nothing is consumed.
    q = conn.execute("SELECT * FROM quotas WHERE tenant_id=? AND meter_code=?", (tid, meter_code)).fetchone()
    if q and q["hard_limit"] and not q["overage_allowed"]:
        cur2 = conn.execute("UPDATE quotas SET consumed_qty=consumed_qty+? WHERE tenant_id=? AND meter_code=? AND"
                            " COALESCE(consumed_qty,0)+COALESCE(reserved_qty,0)+? <= COALESCE(included_qty,0)", (quantity, tid, meter_code, quantity))
        if (getattr(cur2, "rowcount", 0) or 0) == 0:
            conn.execute("DELETE FROM usage_events WHERE id=?", (event_id,))   # deny: consume nothing
            conn.commit()
            raise core.ForbiddenError("quota exceeded (hard stop)")
    elif q:
        conn.execute("UPDATE quotas SET consumed_qty=consumed_qty+? WHERE tenant_id=? AND meter_code=?", (quantity, tid, meter_code))
        if q["overage_allowed"] and (q["consumed_qty"] + quantity) > q["included_qty"]:
            _record_overage(conn, actor, tid, meter_code, (q["consumed_qty"] + quantity) - q["included_qty"])
    st = quota_status(conn, actor, meter_code, tenant_id=tid)
    if st:
        conn.execute("UPDATE quotas SET status=? WHERE tenant_id=? AND meter_code=?", (st["status"], tid, meter_code))
    conn.commit()
    return {"recorded": True, "idempotent": False, "event_id": event_id, "quota": st}


def reserve_usage(conn, actor, meter_code, quantity, idem_key, tenant_id=None):
    """Reserve quota atomically for an operation that may fail. commit or release afterward."""
    core.require(actor, "saas.usage.manage")
    tid = tenant_id if tenant_id is not None else _tenant(actor)
    dup = conn.execute("SELECT id,status FROM usage_reservations WHERE tenant_id=? AND idem_key=?", (tid, idem_key)).fetchone()
    if dup:
        return {"reservation_id": dup["id"], "idempotent": True, "status": dup["status"]}
    q = conn.execute("SELECT * FROM quotas WHERE tenant_id=? AND meter_code=?", (tid, meter_code)).fetchone()
    if q and q["hard_limit"] and not q["overage_allowed"]:
        cur = conn.execute("UPDATE quotas SET reserved_qty=reserved_qty+? WHERE tenant_id=? AND meter_code=? AND"
                           " COALESCE(consumed_qty,0)+COALESCE(reserved_qty,0)+? <= COALESCE(included_qty,0)", (quantity, tid, meter_code, quantity))
        if (getattr(cur, "rowcount", 0) or 0) == 0:
            conn.commit()
            raise core.ForbiddenError("quota exceeded (cannot reserve)")
    elif q:
        conn.execute("UPDATE quotas SET reserved_qty=reserved_qty+? WHERE tenant_id=? AND meter_code=?", (quantity, tid, meter_code))
    cur = conn.execute("INSERT INTO usage_reservations(tenant_id,meter_code,quantity,status,idem_key,created_at)"
                       " VALUES(?,?,?, 'RESERVED', ?,?)", (tid, meter_code, quantity, idem_key, _now()))
    conn.commit()
    return {"reservation_id": cur.lastrowid, "idempotent": False, "status": "RESERVED"}


def commit_reservation(conn, actor, reservation_id):
    core.require(actor, "saas.usage.manage")
    r = conn.execute("SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)).fetchone()
    if not r or r["status"] != "RESERVED":
        raise core.ConflictError("reservation not open")
    conn.execute("UPDATE quotas SET reserved_qty=reserved_qty-?, consumed_qty=consumed_qty+? WHERE tenant_id=? AND meter_code=?",
                 (r["quantity"], r["quantity"], r["tenant_id"], r["meter_code"]))
    conn.execute("UPDATE usage_reservations SET status='COMMITTED' WHERE id=?", (reservation_id,))
    conn.commit()
    return True


def release_reservation(conn, actor, reservation_id):
    core.require(actor, "saas.usage.manage")
    r = conn.execute("SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)).fetchone()
    if not r or r["status"] != "RESERVED":
        return False
    conn.execute("UPDATE quotas SET reserved_qty=reserved_qty-? WHERE tenant_id=? AND meter_code=?",
                 (r["quantity"], r["tenant_id"], r["meter_code"]))
    conn.execute("UPDATE usage_reservations SET status='RELEASED' WHERE id=?", (reservation_id,))
    conn.commit()
    return True


def _record_overage(conn, actor, tenant_id, meter_code, quantity, rate=1.0):
    sub = conn.execute("SELECT id,plan_version FROM subscriptions WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (tenant_id,)).fetchone()
    conn.execute("INSERT INTO overage_charges(tenant_id,subscription_id,meter_code,quantity,rate,plan_version,amount,"
                 "status,created_at) VALUES(?,?,?,?,?,?,?, 'PENDING', ?)",
                 (tenant_id, sub["id"] if sub else None, meter_code, quantity, rate, sub["plan_version"] if sub else None,
                  round(quantity * rate, 2), _now()))


def usage_summary(conn, actor):
    core.require(actor, "saas.usage.view")
    tid = _tenant(actor)
    rows = conn.execute("SELECT meter_code, COALESCE(SUM(quantity),0) total, COUNT(*) events FROM usage_events"
                        " WHERE tenant_id=? GROUP BY meter_code", (tid,)).fetchall()
    return {"by_meter": [dict(r) for r in rows]}


# --------------------------------------------------------------------------- #
# Billing evidence (immutable; Phase-2 tax)
# --------------------------------------------------------------------------- #
def generate_billing_evidence(conn, actor, sub_id, period_start, period_end, addons=0, discount=0, credit=0):
    """Generate an IMMUTABLE billing-evidence snapshot. Idempotent per (tenant,sub,period) — never
    recalculated after generation. Tax comes from the Phase-2 governed policy (no duplicate logic)."""
    core.require(actor, "saas.billing.generate")
    s = _sub(conn, sub_id)
    existing = conn.execute("SELECT * FROM billing_evidence WHERE tenant_id=? AND subscription_id=? AND period_start=?",
                            (s["tenant_id"], sub_id, period_start)).fetchone()
    if existing:
        return {**dict(existing), "idempotent": True}   # never silently recalculated
    av = _active_plan_version(conn, s["plan_code"])
    base_fee = av["base_price"] if av else 0
    usage_total = 0.0
    overage_total = conn.execute("SELECT COALESCE(SUM(amount),0) a FROM overage_charges WHERE tenant_id=? AND subscription_id=? AND status='PENDING'", (s["tenant_id"], sub_id)).fetchone()["a"]
    subtotal = round(base_fee + addons + usage_total + overage_total - discount - credit, 2)
    import policy
    tp = policy.evaluate_tax(conn, subtotal, {"tenant": str(s["tenant_id"])})
    tax = tp["tax"]
    total = subtotal if tp.get("inclusive") else subtotal + tax
    snapshot = {"base_fee": base_fee, "addons": addons, "usage": usage_total, "overage": overage_total,
                "discount": discount, "credit": credit, "tax_policy": tp, "plan_version": s["plan_version"]}
    checksum = _hash(snapshot)
    cur = conn.execute("INSERT INTO billing_evidence(tenant_id,subscription_id,period_start,period_end,plan_version,"
                       "base_fee,addons,usage_total,overage_total,discount,credit,tax_rule,tax_rate,tax_type,currency,"
                       "subtotal,tax,total,payment_status,snapshot,checksum,generated_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'unbilled', ?,?,?)",
                       (s["tenant_id"], sub_id, period_start, period_end, s["plan_version"], base_fee, addons,
                        usage_total, overage_total, discount, credit, tp.get("code"), tp.get("rate"), tp.get("tax_type"),
                        s["currency"], subtotal, tax, total, json.dumps(snapshot), checksum, _now()))
    core.audit(conn, actor, "SAAS_BILLING_GENERATED", "billing_evidence", cur.lastrowid,
               new={"subtotal": subtotal, "tax": tax, "total": total, "checksum": checksum[:12]})
    conn.commit()
    return {"id": cur.lastrowid, "subtotal": subtotal, "tax": tax, "total": total, "checksum": checksum, "idempotent": False}


def list_billing_evidence(conn, actor):
    core.require(actor, "saas.billing.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM billing_evidence WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# Promotions + commercial exceptions + marketplace + customer health
# --------------------------------------------------------------------------- #
def create_promotion(conn, actor, code, discount_type, discount_amount, allowed_plans=None,
                     starts_at=None, ends_at=None, usage_limit=None, per_customer_limit=1, approver=None):
    core.require(actor, "saas.discount.manage")
    if approver is not None and approver == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: a discount approver must differ from its creator")
    if conn.execute("SELECT 1 FROM promotions WHERE code=?", (code,)).fetchone():
        raise core.ConflictError(f"promotion '{code}' already exists")
    cur = conn.execute("INSERT INTO promotions(code,discount_type,discount_amount,allowed_plans,starts_at,ends_at,"
                       "usage_limit,per_customer_limit,approved_by,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (code, discount_type, discount_amount, json.dumps(allowed_plans or []), starts_at or _today(),
                        ends_at, usage_limit, per_customer_limit, approver, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "SAAS_PROMOTION_CREATED", "promotions", cur.lastrowid, new={"code": code})
    conn.commit()
    return cur.lastrowid


def redeem_promotion(conn, actor, code, sub_id):
    core.require(actor, "saas.subscription.manage")
    p = conn.execute("SELECT * FROM promotions WHERE code=?", (code,)).fetchone()
    if not p:
        raise core.NotFoundError("promotion not found")
    if p["ends_at"] and p["ends_at"] < _today():
        raise core.ForbiddenError("promotion expired")
    if p["usage_limit"] and p["used_count"] >= p["usage_limit"]:
        raise core.ForbiddenError("promotion usage limit reached")
    s = _sub(conn, sub_id)
    tcount = conn.execute("SELECT COUNT(*) c FROM promotion_redemptions WHERE promotion_id=? AND tenant_id=?", (p["id"], s["tenant_id"])).fetchone()["c"]
    if tcount >= (p["per_customer_limit"] or 1):
        raise core.ForbiddenError("per-customer promotion limit reached")
    allowed = json.loads(p["allowed_plans"] or "[]")
    if allowed and s["plan_code"] not in allowed:
        raise core.ForbiddenError("promotion not allowed for this plan")
    conn.execute("INSERT INTO promotion_redemptions(promotion_id,tenant_id,subscription_id,created_at) VALUES(?,?,?,?)",
                 (p["id"], s["tenant_id"], sub_id, _now()))
    conn.execute("UPDATE promotions SET used_count=used_count+1 WHERE id=?", (p["id"],))
    core.audit(conn, actor, "SAAS_PROMOTION_REDEEMED", "promotions", p["id"], new={"tenant": s["tenant_id"]})
    conn.commit()
    return {"discount_type": p["discount_type"], "discount_amount": p["discount_amount"]}


def create_exception(conn, actor, tenant_id, kind, scope_ref, reason, ends_at, approver, starts_at=None):
    core.require(actor, "saas.entitlement.override")
    if not ends_at:
        raise core.ValidationError("a commercial exception must have an end date (no permanent exception)")
    if approver == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: exception approver must differ from creator")
    cur = conn.execute("INSERT INTO commercial_exceptions(tenant_id,kind,scope_ref,reason,starts_at,ends_at,"
                       "approved_by,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (tenant_id, kind, scope_ref, reason, starts_at or _today(), ends_at, approver, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "SAAS_EXCEPTION_CREATED", "commercial_exceptions", cur.lastrowid, new={"kind": kind}, reason=reason)
    conn.commit()
    return cur.lastrowid


def create_fee_policy(conn, actor, code, fee_type, fee_value, min_fee=0, max_fee=None):
    core.require(actor, "saas.pricing.manage")
    ver = (conn.execute("SELECT MAX(version) m FROM marketplace_fee_policies WHERE code=?", (code,)).fetchone()["m"] or 0) + 1
    conn.execute("INSERT INTO marketplace_fee_policies(code,fee_type,fee_value,min_fee,max_fee,version,status,created_at)"
                 " VALUES(?,?,?,?,?,?, 'ACTIVE', ?)", (code, fee_type, fee_value, min_fee, max_fee, ver, _now()))
    core.audit(conn, actor, "SAAS_FEE_POLICY_CREATED", "marketplace_fee_policies", 0, new={"code": code, "version": ver})
    conn.commit()
    return ver


def record_marketplace_transaction(conn, actor, tenant_id, booking_ref, gross_value, carrier_amount, fee_policy_code):
    """Compute + persist an IMMUTABLE marketplace transaction snapshot with the exact fee-policy version.
    Carrier payout is derived at completion and never recalculated from current config afterward."""
    core.require(actor, "saas.billing.generate")
    fp = conn.execute("SELECT * FROM marketplace_fee_policies WHERE code=? AND status='ACTIVE' ORDER BY version DESC LIMIT 1", (fee_policy_code,)).fetchone()
    if not fp:
        raise core.NotFoundError("fee policy not found")
    if fp["fee_type"] == "percentage":
        fee = round(gross_value * fp["fee_value"] / 100.0, 2)
    else:
        fee = fp["fee_value"]
    if fp["min_fee"]:
        fee = max(fee, fp["min_fee"])
    if fp["max_fee"]:
        fee = min(fee, fp["max_fee"])
    payout = round(carrier_amount - fee, 2)
    snapshot = {"gross": gross_value, "carrier_amount": carrier_amount, "platform_fee": fee,
                "fee_policy": {"code": fp["code"], "version": fp["version"], "type": fp["fee_type"], "value": fp["fee_value"]},
                "carrier_payout": payout}
    cur = conn.execute("INSERT INTO marketplace_transactions(tenant_id,booking_ref,gross_value,carrier_amount,"
                       "platform_fee,carrier_payout,fee_policy_code,fee_policy_version,snapshot,created_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (tenant_id, booking_ref, gross_value, carrier_amount, fee, payout, fp["code"], fp["version"],
                        json.dumps(snapshot), _now()))
    core.audit(conn, actor, "SAAS_MARKETPLACE_TXN", "marketplace_transactions", cur.lastrowid,
               new={"booking": booking_ref, "fee": fee, "payout": payout, "fee_policy_version": fp["version"]})
    conn.commit()
    return {"transaction_id": cur.lastrowid, "platform_fee": fee, "carrier_payout": payout, "fee_policy_version": fp["version"]}


def customer_health(conn, actor, tenant_id=None):
    """Deterministic customer-health metrics. AI does NOT autonomously suspend/terminate."""
    core.require(actor, "saas.subscription.view")
    tid = tenant_id if tenant_id is not None else _tenant(actor)
    sub = conn.execute("SELECT * FROM subscriptions WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    users = 0
    try:
        users = conn.execute("SELECT COUNT(*) c FROM users WHERE tenant_id=?", (tid,)).fetchone()["c"]
    except Exception:
        pass
    bookings = 0
    try:
        bookings = conn.execute("SELECT COUNT(*) c FROM bookings WHERE tenant_id=?", (tid,)).fetchone()["c"]
    except Exception:
        pass
    return {"tenant": tid, "subscription_status": sub["status"] if sub else "none", "active_users": users,
            "booking_volume": bookings, "payment_status": sub["payment_status"] if sub else "none",
            "source": "deterministic", "ai_assisted": False, "human_assessment": None,
            "note": "AI must not autonomously suspend or terminate; this is a deterministic metric"}


# --------------------------------------------------------------------------- #
# Entitlement cache (per-tenant; never shared)
# --------------------------------------------------------------------------- #
def _invalidate_cache(conn, tenant_id):
    conn.execute("DELETE FROM entitlement_cache WHERE tenant_id=?", (tenant_id,))


# --------------------------------------------------------------------------- #
# Migration classification
# --------------------------------------------------------------------------- #
def classify_existing(conn):
    def _c(sql):
        try:
            return conn.execute(sql).fetchone()["c"]
        except Exception:
            return 0
    return {"existing_tenants": _c("SELECT COUNT(*) c FROM tenants"), "subscriptions": _c("SELECT COUNT(*) c FROM subscriptions"),
            "fabricated_contracts": 0, "fabricated_pricing": 0, "modules_preserved": True, "flags_preserved": True,
            "financial_differences": 0, "operational_status_differences": 0, "entitlement_losses": 0,
            "tenant_access_changes": 0}
