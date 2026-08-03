"""RGO OS backend — minimal stdlib HTTP API over the core service layer.

Real server-side API (no framework needed to run). Auth via Bearer token from
POST /login. Every handler enforces authorization inside the service layer.
AppError subclasses map to proper HTTP status codes. Swap this thin layer for
FastAPI/Flask later without touching core.py.

Run:  python server.py           # serves on http://127.0.0.1:8787 with a seeded demo DB
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import core
import ops
import admin
import catalog   # noqa: F401  (ensures full schema/roles registered)
import pdfgen
import admin_platform
import org
import tenant
import db

# --- configuration (never hard-coded; env-driven) --------------------------
APP_ENV = os.environ.get("APP_ENV", "development")
DEBUG = os.environ.get("APP_DEBUG", "false").lower() == "true"
PORT = int(os.environ.get("PORT", "8787"))
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
APP_SECRET = os.environ.get("APP_SECRET")

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}')
log = logging.getLogger("rgo")


def validate_config():
    """Fail fast + safe when required production configuration is missing."""
    if APP_ENV == "production":
        missing = [k for k in ("APP_SECRET", "DATABASE_URL", "CORS_ORIGINS") if not os.environ.get(k)]
        if missing:
            log.error("startup blocked: missing required config: %s", ",".join(missing))
            sys.exit(2)


validate_config()
import threading
_conn = db.connect(os.environ.get("DATABASE_URL"))   # sqlite (dev) or postgres (prod)
_DB_LOCK = threading.Lock()                          # serialize DB access across worker threads
_store = pdfgen.MemStore()                           # swap for S3/local disk in prod


def _seed_users():
    try:
        core.create_user(_conn, "admin@rgo.demo", "demo1234", "admin", "Admin")
        core.create_user(_conn, "est@rgo.demo", "demo1234", "estimator", "Estimator")
        core.create_user(_conn, "appr@rgo.demo", "demo1234", "approver", "Approver")
        core.create_user(_conn, "fin@rgo.demo", "demo1234", "finance", "Finance")
    except core.ConflictError:
        pass


_seed_users()
_provider = core.MockWiseProvider()


def _actor(handler):
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise core.AuthError("missing bearer token")
    actor = core.actor_for(_conn, auth[7:])
    admin_platform.apply_rbac(_conn, actor)   # C-005: data-driven RBAC (flag-gated, reversible)
    return actor


# route table: (METHOD, path) -> handler(actor, body, params)
def _routes():
    def login(actor, body, _):
        # C-007: guarded login (lockout -> credentials -> status -> MFA -> session + history)
        return {"token": admin_platform.guarded_login(
            _conn, body["email"], body["password"], mfa_code=body.get("mfa_code"))}

    def create_customer(actor, body, _):
        return {"id": core.create_customer(_conn, actor, body["name"], body.get("contact"), body.get("email"))}

    def create_booking(actor, body, _):
        return {"id": core.create_booking(_conn, actor, body["customer_id"], body.get("service"),
                                          body.get("cargo"), body.get("weight"),
                                          body.get("from"), body.get("to"), body.get("date"))}

    def get_booking(actor, body, p):
        return core.get_booking(_conn, actor, int(p["id"]))

    def review(actor, body, p):
        core.review_booking(_conn, actor, int(p["id"])); return {"ok": True}

    def ready(actor, body, p):
        core.ready_for_quotation(_conn, actor, int(p["id"])); return {"ok": True}

    def create_quote(actor, body, p):
        return {"id": core.create_quotation(_conn, actor, int(p["id"]), body["lines"],
                                            body.get("discount_pct", 0), body.get("dp_pct"),
                                            body.get("est_cost", 0))}

    def submit_quote(actor, body, p):
        return {"status": core.submit_quotation(_conn, actor, int(p["id"]))}

    def approve_quote(actor, body, p):
        core.approve_quotation(_conn, actor, int(p["id"])); return {"ok": True}

    def send_quote(actor, body, p):
        core.send_quotation(_conn, actor, int(p["id"])); return {"ok": True}

    def accept_quote(actor, body, p):
        core.accept_quotation(_conn, actor, int(p["id"]), body["accepted_by"],
                              body.get("position"), body.get("terms_version", "v1")); return {"ok": True}

    def pay_request(actor, body, p):
        return {"id": core.create_payment_request(_conn, actor, int(p["id"]), _provider)}

    def pay_link(actor, body, p):
        return {"link": core.register_payment_link(_conn, actor, int(p["id"]), _provider)}

    def pay_evidence(actor, body, p):
        core.submit_payment_evidence(_conn, actor, int(p["id"]), body.get("proof", "receipt")); return {"ok": True}

    def pay_verify(actor, body, p):
        return {"status": core.verify_payment(_conn, actor, int(p["id"]), body["amount_received"],
                                             body["txn_ref"], body.get("fees", 0), body.get("notes"))}

    def confirm(actor, body, p):
        return {"job_no": core.confirm_job(_conn, actor, int(p["id"]))}

    def audit(actor, body, p):
        core.require(actor, "booking.read")
        return {"audit": core.list_audit(_conn, "booking", int(p["id"]))}

    return {
        ("POST", "/login"): login,
        ("POST", "/customers"): create_customer,
        ("POST", "/bookings"): create_booking,
        ("GET", "/bookings/:id"): get_booking,
        ("POST", "/bookings/:id/review"): review,
        ("POST", "/bookings/:id/ready"): ready,
        ("POST", "/bookings/:id/quotation"): create_quote,
        ("POST", "/quotations/:id/submit"): submit_quote,
        ("POST", "/quotations/:id/approve"): approve_quote,
        ("POST", "/quotations/:id/send"): send_quote,
        ("POST", "/quotations/:id/accept"): accept_quote,
        ("POST", "/bookings/:id/payment-request"): pay_request,
        ("POST", "/payments/:id/link"): pay_link,
        ("POST", "/payments/:id/evidence"): pay_evidence,
        ("POST", "/payments/:id/verify"): pay_verify,
        ("POST", "/bookings/:id/confirm"): confirm,
        ("GET", "/bookings/:id/audit"): audit,
    }


def _ops_routes():
    def reserve(actor, body, p):
        return {"id": ops.reserve_resource(_conn, actor, int(p["id"]), body["resource_type"],
                                           body["resource_ref"], body.get("confirmed", False))}

    def job_transition(actor, body, p):
        return {"status": ops.transition_job(_conn, actor, int(p["id"]), body["to_status"],
                                            evidence=body.get("evidence"), reason=body.get("reason"))}

    def change_order(actor, body, p):
        return {"id": ops.create_change_order(_conn, actor, int(p["id"]), body["reason"],
                                             body["amount"], body.get("tax", 0))}

    def co_approve(actor, body, p):
        ops.approve_change_order(_conn, actor, int(p["id"])); return {"ok": True}

    def expense(actor, body, p):
        return {"id": ops.add_expense(_conn, actor, int(p["id"]), body["category"], body["amount"],
                                     body.get("supplier"))}

    def final_invoice(actor, body, p):
        return {"id": ops.generate_final_invoice(_conn, actor, int(p["id"]), body.get("due_date"))}

    def allocate(actor, body, p):
        return ops.allocate_payment(_conn, actor, int(p["id"]), body["amount"], body["ref"])

    def profitability(actor, body, p):
        core.require(actor, "job.read"); return ops.job_profitability(_conn, int(p["id"]), actor)

    def safety(actor, body, p):
        return {"id": admin.safety_record(_conn, actor, int(p["id"]), body["result"], notes=body.get("notes"))}

    def inv_move(actor, body, p):
        return {"qty": admin.inv_move(_conn, actor, int(p["id"]), body["kind"], body["qty"], body.get("ref"))}

    def reports(actor, body, p):
        core.require(actor, "booking.read")               # tenant-scoped aggregates (no cross-tenant leak)
        return {"quotation_conversion": ops.report_quotation_conversion(_conn, actor),
                "receivables": ops.report_receivables(_conn, actor),
                "confirmed_jobs": ops.report_confirmed_jobs(_conn, actor),
                "awaiting_payment": ops.report_accepted_awaiting_payment(_conn, actor)}

    return {
        ("POST", "/bookings/:id/reserve"): reserve,
        ("POST", "/jobs/:id/transition"): job_transition,
        ("POST", "/jobs/:id/change-order"): change_order,
        ("POST", "/change-orders/:id/approve"): co_approve,
        ("POST", "/jobs/:id/expense"): expense,
        ("POST", "/jobs/:id/invoice"): final_invoice,
        ("POST", "/invoices/:id/allocate"): allocate,
        ("GET", "/jobs/:id/profitability"): profitability,
        ("POST", "/jobs/:id/safety"): safety,
        ("POST", "/inventory/:id/move"): inv_move,
        ("GET", "/reports"): reports,
    }


def _phase2_routes():
    import base64

    def pdf_gen(actor, body, p):
        r = pdfgen.generate_quotation_pdf(_conn, actor, int(p["id"]), _store)
        return {"ref": r["ref"], "size": r["size"], "doc_id": r["doc_id"]}

    def pdf_get(actor, body, p):
        data = pdfgen.get_quotation_pdf(_conn, actor, int(p["id"]), _store)
        return {"content_type": "application/pdf", "pdf_base64": base64.b64encode(data).decode()}

    def calendar(actor, body, p):
        return ops.calendar(_conn, actor, body.get("start"), body.get("end"))

    def inv_lines(actor, body, p):
        core.require(actor, "invoice.create")
        tenant.guard(actor, _conn.execute("SELECT * FROM invoices WHERE id=?", (int(p["id"]),)).fetchone())
        return {"lines": ops.invoice_lines(_conn, int(p["id"]))}

    return {
        ("POST", "/quotations/:id/pdf"): pdf_gen,
        ("GET", "/quotations/:id/pdf"): pdf_get,
        ("GET", "/calendar"): calendar,
        ("GET", "/invoices/:id/lines"): inv_lines,
    }


def _rows(rowlist):
    return [dict(r) for r in rowlist]


def _row(r):
    return dict(r) if r else None


def _tid():
    t = admin_platform.get_tenant(_conn, "RGO")
    return t["id"] if t else 1


def _admin_routes():
    """Enterprise Administration console API (Platform 1). Read + key-mutation endpoints
    backing the admin menu (Organization / People & Access / Calendars / Security /
    Configuration / Governance). Permission-gated; org-scoped where relevant."""
    R = core.require

    # ---- Organization -----------------------------------------------------
    def org_tree(a, b, p):       R(a, "org.view");   return {"tree": org.tree(_conn, _tid())}
    def org_units(a, b, p):      R(a, "org.view");   return {"units": _rows(org.list_units(_conn, _tid(), kind=b.get("kind"), status=b.get("status"), q=b.get("q")))}
    def org_unit_create(a, b, p):R(a, "org.manage"); return {"id": org.create_unit(_conn, a, _tid(), b["kind"], b["code"], b["name"], parent_id=b.get("parent_id"), description=b.get("description"), effective_from=b.get("effective_from"), effective_to=b.get("effective_to"))}
    def org_reparent(a, b, p):   R(a, "org.manage"); org.reparent(_conn, a, int(p["id"]), b.get("parent_id")); return {"ok": True}
    def org_status(a, b, p):     R(a, "org.manage"); org.set_status(_conn, a, int(p["id"]), b["status"]); return {"ok": True}
    def cost_centers(a, b, p):   R(a, "org.view");   return {"cost_centers": _rows(org.list_cost_centers(_conn, _tid(), status=b.get("status")))}
    def cc_create(a, b, p):      R(a, "org.manage"); return {"id": org.create_cost_center(_conn, a, _tid(), b["code"], b["name"], branch_id=b.get("branch_id"), department_id=b.get("department_id"), budget_ref=b.get("budget_ref"), external_code=b.get("external_code"))}
    def profile_get(a, b, p):    R(a, "org.view");   return {"profile": _row(org.company_profile(_conn, _tid()))}
    def profile_set(a, b, p):    R(a, "org.manage"); org.upsert_company_profile(_conn, a, _tid(), **{k: v for k, v in b.items()}); return {"ok": True}

    # ---- People & Access --------------------------------------------------
    def users(a, b, p):          R(a, "user_admin.view");   return {"users": _rows(admin_platform.list_users(_conn))}
    def user_create(a, b, p):    R(a, "user_admin.manage"); return {"id": admin_platform.create_user(_conn, a, b["email"], b["password"], b["role"], b.get("name"))}
    def user_status(a, b, p):    R(a, "user_admin.manage"); admin_platform.set_status(_conn, a, int(p["id"]), b["status"]); return {"ok": True}
    def user_reset(a, b, p):     R(a, "user_admin.manage"); admin_platform.reset_password(_conn, a, int(p["id"]), b["password"]); return {"ok": True}
    def roles(a, b, p):          R(a, "role_admin.view");   return {"roles": _rows(admin_platform.list_roles(_conn, "RGO"))}
    def role_create(a, b, p):    R(a, "role_admin.manage"); return {"id": admin_platform.create_role(_conn, "RGO", b["code"], b["name"], layer=b.get("layer", 4), grants=set(b.get("grants", [])), actor=a)}
    def user_assign_role(a, b, p):  # guardrails: self-elevation / platform-role restriction
        R(a, "role_admin.manage")
        role = admin_platform.role_by_code(_conn, "RGO", b["role"])
        if not role:
            raise core.ConflictError("unknown role")
        admin_platform.assign_role(_conn, int(p["id"]), role["id"], actor=a,
                                   allow_sod_exception=b.get("allow_sod_exception", False),
                                   reason=b.get("reason")); return {"ok": True}
    def permissions(a, b, p):    R(a, "role_admin.view");   return {"permissions": _rows(_conn.execute("SELECT code,module,action,description FROM admin_permissions ORDER BY module,action").fetchall())}
    def role_clone(a, b, p):     R(a, "role_admin.manage"); return {"id": admin_platform.clone_role(_conn, "RGO", p["code"], b["new_code"], b["name"], actor=a)}
    def reparent_preview(a, b, p): R(a, "org.view");         return org.reparent_preview(_conn, int(p["id"]), b.get("new_parent_id"))
    def config_history(a, b, p):
        R(a, "admin.configuration.history.view")
        key = b.get("key")
        sql = "SELECT ts,actor,new_value,correlation_id FROM audit_logs WHERE action='CONFIG_SET'"
        args = []
        if key:
            sql += " AND new_value LIKE ?"; args.append('%"key": "' + key + '"%')
        return {"history": _rows(_conn.execute(sql + " ORDER BY id DESC LIMIT 100", tuple(args)).fetchall())}
    def assignments(a, b, p):    R(a, "user_admin.view");   return {"assignments": _rows(org.user_assignments(_conn, int(p["id"])))}
    def assign(a, b, p):         R(a, "user_admin.manage"); return {"id": org.assign_user(_conn, a, _tid(), int(p["id"]), b["scope_kind"], b["scope_id"], b.get("assignment_type", "PRIMARY"), reason=b.get("reason"))}
    def sessions(a, b, p):       R(a, "security.view");     return {"sessions": _rows(admin_platform.list_sessions(_conn))}
    def session_revoke(a, b, p): R(a, "security.manage");   admin_platform.revoke_session(_conn, b["token"], actor=a); return {"ok": True}
    def login_history(a, b, p):  R(a, "security.view");     return {"history": _rows(admin_platform.list_login_history(_conn, limit=b.get("limit", 100)))}
    def mfa_status(a, b, p):     R(a, "user_admin.view");   return {"enrolled": admin_platform.mfa_enrolled(_conn, int(p["id"]))}
    def effective_access(a, b, p):
        R(a, "user_admin.view")
        uid = int(p["id"])
        roles = _rows(admin_platform.user_roles(_conn, uid))
        grants = []
        for r in roles:
            role = admin_platform.role_by_code(_conn, "RGO", r["code"])
            if role:
                for g in sorted(admin_platform.effective_role_grants(_conn, role["id"])):
                    grants.append({"permission": g, "module": g.split(".")[0], "source_role": r["code"],
                                   "layer": r["layer"], "source": "wildcard" if g.endswith("*") else "explicit",
                                   "decision": "allow"})
        if b.get("module"):                               # filters (Item 4)
            grants = [g for g in grants if g["module"] == b["module"]]
        if b.get("source_role"):
            grants = [g for g in grants if g["source_role"] == b["source_role"]]
        eff = sorted(admin_platform.effective_permissions(_conn, uid))
        u = admin_platform.get_user(_conn, uid)
        urow = _conn.execute("SELECT tenant_id FROM users WHERE id=?", (uid,)).fetchone()
        t = admin_platform.get_tenant(_conn, "RGO")
        import datetime
        return {"user_id": uid, "roles": roles, "grants": grants, "effective_permissions": eff,
                "user_status": (u["status"] if u else None),
                "tenant": (urow["tenant_id"] if urow else None),
                "tenant_status": (t["status"] if t else None), "cache_state": "live",
                "sod_conflicts": admin_platform.sod_conflicts(set(eff)),
                "recalculated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}

    def access_check(a, b, p):
        R(a, "user_admin.view")                           # decision matches route enforcement
        perm = b["permission"]
        allowed = admin_platform.has_permission(_conn, int(p["id"]), perm)
        return {"user_id": int(p["id"]), "permission": perm, "decision": "allow" if allowed else "deny",
                "reason": "granted by role" if allowed else "deny-by-default (no matching grant)"}

    def role_compare(a, b, p):     R(a, "role_admin.view"); return admin_platform.compare_roles(_conn, "RGO", b["a"], b["b"])
    def role_dependency(a, b, p):  R(a, "role_admin.view"); return admin_platform.role_dependency(_conn, "RGO", p["code"])
    def wcal_conflicts(a, b, p):   R(a, "org.view");        return org.working_calendar_conflicts(_conn, int(p["id"]))
    def cfg_preview(a, b, p):
        R(a, "admin.configuration.simulate")              # non-mutating override preview
        return org.effective_config_preview(_conn, b["key"], b["scope"], b.get("scope_ref", ""), b["value"],
                                            tenant=b.get("tenant"), business_unit=b.get("business_unit"),
                                            branch=b.get("branch"), department=b.get("department"),
                                            team=b.get("team"), user=b.get("user"))

    # ---- Calendars --------------------------------------------------------
    def hol_cals(a, b, p):       R(a, "org.view");   return {"calendars": _rows(_conn.execute("SELECT * FROM holiday_calendars WHERE tenant_id=?", (_tid(),)).fetchall())}
    def hol_cal_create(a, b, p): R(a, "org.manage"); return {"id": org.create_holiday_calendar(_conn, a, _tid(), b["code"], b["name"], scope=b.get("scope", "company"), parent_id=b.get("parent_id"))}
    def hol_days(a, b, p):       R(a, "org.view");   return {"holidays": _rows(org.effective_holidays(_conn, int(p["id"])))}
    def work_cals(a, b, p):      R(a, "org.view");   return {"calendars": _rows(_conn.execute("SELECT * FROM working_calendars WHERE tenant_id=?", (_tid(),)).fetchall())}
    def work_cal_create(a, b, p):R(a, "org.manage"); return {"id": org.create_working_calendar(_conn, a, _tid(), b["code"], b["name"], workdays=b.get("workdays", "Mon,Tue,Wed,Thu,Fri"), shift_start=b.get("shift_start", "08:00"), shift_end=b.get("shift_end", "17:00"), parent_id=b.get("parent_id"))}

    # ---- Security ---------------------------------------------------------
    def sec_policies(a, b, p):
        R(a, "security.view")
        return {"password_policy": admin_platform.password_policy(_conn),
                "mfa_policy": admin_platform.mfa_policy(_conn),
                "authorization_mode": admin_platform.resolve_config(_conn, "iam.rbac_source", tenant="")[0] or "hybrid",
                "lockout": {"threshold": admin_platform.resolve_config(_conn, "auth.lockout_threshold", tenant="")[0],
                            "window_min": admin_platform.resolve_config(_conn, "auth.lockout_window_min", tenant="")[0]}}
    def sec_policy_set(a, b, p):
        R(a, "security.manage")
        for k, v in b.items():
            admin_platform.set_config(_conn, "platform", "", k, v, actor=a)
        return {"ok": True}
    def sec_events(a, b, p):
        R(a, "security.view")
        return {"events": _rows(_conn.execute(
            "SELECT * FROM login_history WHERE success=0 ORDER BY id DESC LIMIT ?", (b.get("limit", 100),)).fetchall())}

    # ---- Configuration ----------------------------------------------------
    def cfg_effective(a, b, p):
        R(a, "admin.configuration.view")
        return org.resolve_org_config(_conn, b["key"], tenant=b.get("tenant"), business_unit=b.get("business_unit"),
                                      branch=b.get("branch"), department=b.get("department"), team=b.get("team"), user=b.get("user"))
    def cfg_list(a, b, p):       R(a, "admin.configuration.view");   return {"config": _rows(_conn.execute("SELECT scope,scope_ref,key,value,effective_to,updated_at FROM platform_config ORDER BY scope,key").fetchall())}
    def cfg_definitions(a, b, p): R(a, "admin.configuration.view"); import config_registry; return {"definitions": _rows(config_registry.list_definitions(_conn))}
    def policy_simulate(a, b, p):
        R(a, "admin.configuration.simulate"); import policy   # non-mutating policy decision preview
        ctx = {"tenant": b.get("tenant"), "business_unit": b.get("business_unit"), "branch": b.get("branch")}
        kind = b.get("policy")
        if kind == "tax":         res = policy.evaluate_tax(_conn, float(b.get("taxable", 0)), ctx)
        elif kind == "downpayment": res = policy.evaluate_downpayment(_conn, float(b.get("total", 0)), ctx, requested_rate=b.get("rate"))
        elif kind == "approval":  res = policy.evaluate_approval(_conn, float(b.get("total", 0)), float(b.get("discount_pct", 0)), ctx)
        else: raise core.ValidationError("policy must be tax | downpayment | approval")
        core.audit(_conn, a, "POLICY_SIMULATED", "config", 0, new={"policy": kind, "ctx": ctx}); _conn.commit()
        return res
    def cfg_set(a, b, p):        R(a, "admin.configuration.value.manage"); admin_platform.set_config(_conn, b["scope"], b.get("scope_ref", ""), b["key"], b["value"], actor=a, effective_to=b.get("effective_to")); return {"ok": True}

    # ---- Governance -------------------------------------------------------
    def audit_trail(a, b, p):
        R(a, "audit.view")                                # server-side filters (Item 7)
        where, args = ["1=1"], []
        for col in ("correlation_id", "action", "entity", "role"):
            if b.get(col):
                where.append(col + "=?"); args.append(b[col])
        if b.get("actor") is not None:
            where.append("actor=?"); args.append(b["actor"])
        if b.get("entity_id") is not None:
            where.append("entity_id=?"); args.append(b["entity_id"])
        if b.get("start"):
            where.append("ts>=?"); args.append(b["start"])
        if b.get("end"):
            where.append("ts<=?"); args.append(b["end"])
        args.append(b.get("limit", 100))
        return {"audit": _rows(_conn.execute(
            "SELECT ts,actor,role,action,entity,entity_id,reason,correlation_id FROM audit_logs"
            " WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?", tuple(args)).fetchall())}
    def backfill_status(a, b, p):
        R(a, "audit.view"); import backfill
        return backfill.status(_conn)

    def backfill_analyze(a, b, p):
        R(a, "audit.view"); import backfill
        return backfill.analyze(_conn)

    def backfill_dry_run(a, b, p):
        R(a, "audit.view"); import backfill
        return backfill.dry_run(_conn)

    def backfill_execute(a, b, p):
        R(a, "tenant.manage"); import backfill
        return backfill.execute(_conn, a)

    def backfill_remediation(a, b, p):
        R(a, "audit.view"); import backfill
        return {"remediation": _rows(_conn.execute(
            "SELECT * FROM org_backfill_remediation ORDER BY status, table_name").fetchall())}

    def remediation_resolve(a, b, p):
        R(a, "tenant.manage"); import backfill
        backfill.resolve_remediation(_conn, a, int(p["id"])); return {"ok": True}

    def cross_access(a, b, p):
        # Governed, EXPIRING platform cross-tenant access (Item 5): explicit permission +
        # target + mandatory reason + short TTL + HIGH-severity audit + correlation id.
        g = tenant.activate_cross_access(_conn, a, b.get("target_tenant"), b.get("reason"), b.get("ttl_seconds"))
        g["granted"] = True
        return g

    def cross_access_terminate(a, b, p):
        R(a, tenant.CROSS_ACCESS_PERMISSION)
        tenant.terminate_cross_access(_conn, a, int(p["id"])); return {"terminated": True}

    def cross_access_list(a, b, p):
        R(a, "audit.view")
        return {"grants": _rows(_conn.execute(
            "SELECT id,user_id,target_tenant,reason,activated_at,expires_at,terminated_at,status"
            " FROM cross_access_grants ORDER BY id DESC LIMIT 100").fetchall())}
    def data_integrity(a, b, p):
        R(a, "audit.view")                                # per-check statuses (Item 6/8)
        import datetime
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        cid = core.correlation_id()

        def chk(code, sql, args=(), warn=False):
            try:
                n = _conn.execute(sql, args).fetchone()["c"]
                status = "PASS" if n == 0 else ("WARNING" if warn else "FAIL")
                return {"check": code, "executed_at": now_iso, "findings": n,
                        "severity": "low" if warn else "high", "status": status,
                        "recommended_action": "none" if n == 0 else "review",
                        "correlation_id": cid}
            except Exception as e:
                return {"check": code, "executed_at": now_iso, "findings": None,
                        "severity": "unknown", "status": "NOT_RUN", "detail": str(e)[:60],
                        "correlation_id": cid}
        checks = [
            chk("orphan_user_assignments",
                "SELECT COUNT(*) c FROM user_organization_assignments ua LEFT JOIN users u ON u.id=ua.user_id WHERE u.id IS NULL"),
            chk("orphan_org_units",
                "SELECT COUNT(*) c FROM org_units o WHERE o.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM org_units p WHERE p.id=o.parent_id)"),
            chk("tenantless_operational_records",
                "SELECT (SELECT COUNT(*) FROM customers WHERE tenant_id IS NULL)+(SELECT COUNT(*) FROM bookings WHERE tenant_id IS NULL)+(SELECT COUNT(*) FROM jobs WHERE tenant_id IS NULL) c", warn=True),
            chk("stale_active_cross_grants",
                "SELECT COUNT(*) c FROM cross_access_grants WHERE status='ACTIVE' AND terminated_at IS NULL AND expires_at < ?", (now_iso,)),
            chk("unresolved_remediation",
                "SELECT COUNT(*) c FROM org_backfill_remediation WHERE status='OPEN'", warn=True),
            chk("duplicate_user_emails",
                "SELECT COUNT(*) c FROM (SELECT email FROM users GROUP BY email HAVING COUNT(*)>1) x"),
            chk("role_assignments_to_missing_roles",
                "SELECT COUNT(*) c FROM admin_user_roles ur LEFT JOIN admin_roles r ON r.id=ur.role_id WHERE r.id IS NULL"),
            chk("documents_without_owner",
                "SELECT COUNT(*) c FROM documents WHERE entity_id IS NULL"),
            chk("cross_tenant_parent_child_units",
                "SELECT COUNT(*) c FROM org_units c JOIN org_units p ON p.id=c.parent_id WHERE c.tenant_id<>p.tenant_id"),
            chk("users_with_no_tenant",
                "SELECT COUNT(*) c FROM users WHERE tenant_id IS NULL AND role NOT IN ('admin','super_admin','owner')", warn=True),
            chk("active_sessions_for_inactive_users",
                "SELECT COUNT(*) c FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.status<>'ACTIVE'"),
            chk("role_assignments_to_missing_users",
                "SELECT COUNT(*) c FROM admin_user_roles ur LEFT JOIN users u ON u.id=ur.user_id WHERE u.id IS NULL"),
            chk("config_referencing_inactive_units",
                "SELECT COUNT(*) c FROM platform_config pc WHERE pc.scope IN ('branch','department','team','business_unit')"
                " AND NOT EXISTS (SELECT 1 FROM org_units o WHERE CAST(o.id AS TEXT)=pc.scope_ref AND o.status='ACTIVE')", warn=True),
        ]
        fail = [c for c in checks if c["status"] == "FAIL"]
        not_run = [c for c in checks if c["status"] == "NOT_RUN"]
        # not healthy if any required check FAILed or did NOT_RUN (directive)
        return {"checks": checks, "ok": (not fail and not not_run), "executed_at": now_iso,
                "summary": {"total": len(checks), "fail": len(fail),
                            "warning": len([c for c in checks if c["status"] == "WARNING"]),
                            "not_run": len(not_run)}}

    return {
        ("GET", "/admin/org/tree"): org_tree,
        ("POST", "/admin/org/units/search"): org_units,
        ("POST", "/admin/org/units"): org_unit_create,
        ("POST", "/admin/org/units/:id/reparent"): org_reparent,
        ("POST", "/admin/org/units/:id/status"): org_status,
        ("GET", "/admin/org/cost-centers"): cost_centers,
        ("POST", "/admin/org/cost-centers"): cc_create,
        ("GET", "/admin/org/company-profile"): profile_get,
        ("POST", "/admin/org/company-profile"): profile_set,
        ("GET", "/admin/users"): users,
        ("POST", "/admin/users"): user_create,
        ("POST", "/admin/users/:id/status"): user_status,
        ("POST", "/admin/users/:id/reset-password"): user_reset,
        ("GET", "/admin/roles"): roles,
        ("POST", "/admin/roles"): role_create,
        ("GET", "/admin/permissions"): permissions,
        ("POST", "/admin/roles/:code/clone"): role_clone,
        ("POST", "/admin/roles/compare"): role_compare,
        ("GET", "/admin/roles/:code/dependency"): role_dependency,
        ("POST", "/admin/users/:id/access-check"): access_check,
        ("GET", "/admin/working-calendars/:id/conflicts"): wcal_conflicts,
        ("POST", "/admin/config/preview"): cfg_preview,
        ("POST", "/admin/org/units/:id/reparent-preview"): reparent_preview,
        ("GET", "/admin/config/history"): config_history,
        ("GET", "/admin/users/:id/assignments"): assignments,
        ("POST", "/admin/users/:id/assignments"): assign,
        ("GET", "/admin/sessions"): sessions,
        ("POST", "/admin/sessions/revoke"): session_revoke,
        ("GET", "/admin/login-history"): login_history,
        ("GET", "/admin/users/:id/mfa"): mfa_status,
        ("GET", "/admin/users/:id/effective-access"): effective_access,
        ("POST", "/admin/users/:id/roles"): user_assign_role,
        ("POST", "/admin/governance/backfill-remediation/:id/resolve"): remediation_resolve,
        ("POST", "/admin/security/cross-access"): cross_access,
        ("POST", "/admin/security/cross-access/:id/terminate"): cross_access_terminate,
        ("GET", "/admin/security/cross-access"): cross_access_list,
        ("GET", "/admin/holiday-calendars"): hol_cals,
        ("POST", "/admin/holiday-calendars"): hol_cal_create,
        ("GET", "/admin/holiday-calendars/:id/holidays"): hol_days,
        ("GET", "/admin/working-calendars"): work_cals,
        ("POST", "/admin/working-calendars"): work_cal_create,
        ("GET", "/admin/security/policies"): sec_policies,
        ("POST", "/admin/security/policies"): sec_policy_set,
        ("GET", "/admin/security/events"): sec_events,
        ("POST", "/admin/config/effective"): cfg_effective,
        ("GET", "/admin/config"): cfg_list,
        ("GET", "/admin/config/definitions"): cfg_definitions,
        ("POST", "/admin/config/simulate"): policy_simulate,
        ("POST", "/admin/config"): cfg_set,
        ("GET", "/admin/audit"): audit_trail,
        ("GET", "/admin/governance/backfill-status"): backfill_status,
        ("GET", "/admin/governance/backfill-analyze"): backfill_analyze,
        ("POST", "/admin/governance/backfill-dry-run"): backfill_dry_run,
        ("POST", "/admin/governance/backfill-execute"): backfill_execute,
        ("GET", "/admin/governance/backfill-remediation"): backfill_remediation,
        ("GET", "/admin/governance/data-integrity"): data_integrity,
    }


def _phase3_routes():
    """Phase 3 — CRM Administration + shared Master Data Center. Permission-gated, tenant-scoped,
    audited; policy simulation/detection are non-mutating where documented."""
    R = core.require
    import masterdata
    import crm_admin as crm

    # ---- Master Data Center (generic governed lookups) --------------------
    def md_domains(a, b, p):     R(a, "master_data.view");   return {"domains": masterdata.domain_catalog(_conn, a)}
    def md_search(a, b, p):      R(a, "master_data.view");   return {"values": masterdata.list_values(_conn, a, b["domain"], include_inactive=b.get("include_inactive", True), q=b.get("q"))}
    def md_create(a, b, p):
        return {"id": masterdata.create_value(_conn, a, b["domain"], b["code"], b["name"],
                description=b.get("description"), parent_id=b.get("parent_id"), sort_order=b.get("sort_order", 0),
                status=b.get("status", "ACTIVE"), effective_from=b.get("effective_from"),
                effective_to=b.get("effective_to"), system_protected=b.get("system_protected", False),
                metadata=b.get("metadata"))}
    def md_update(a, b, p):      return {"ok": masterdata.update_value(_conn, a, int(p["id"]), **{k: v for k, v in b.items()})}
    def md_status(a, b, p):      return {"ok": masterdata.set_status(_conn, a, int(p["id"]), b["status"], reason=b.get("reason"))}
    def md_deps(a, b, p):        R(a, "master_data.view");   return masterdata.dependencies(_conn, a, int(p["id"]))
    def md_replace(a, b, p):     return masterdata.replace(_conn, a, int(p["id"]), int(b["replacement_id"]), reason=b.get("reason"))
    def md_import(a, b, p):      return masterdata.import_values(_conn, a, b["domain"], b.get("rows", []), dry_run=b.get("dry_run", True))
    def md_export(a, b, p):      return {"domain": b["domain"], "rows": masterdata.export_values(_conn, a, b["domain"])}

    # ---- CRM classifications / pricing (granular crm.admin.* perms) --------
    def crm_class_search(a, b, p): R(a, "crm.admin.classification.view");  return {"values": masterdata.list_values(_conn, a, b["domain"], include_inactive=b.get("include_inactive", True), q=b.get("q"))}
    def crm_class_create(a, b, p): R(a, "crm.admin.classification.manage"); return {"id": masterdata.create_value(_conn, a, b["domain"], b["code"], b["name"], description=b.get("description"), sort_order=b.get("sort_order", 0))}
    def crm_class_status(a, b, p): R(a, "crm.admin.classification.manage"); return {"ok": masterdata.set_status(_conn, a, int(p["id"]), b["status"], reason=b.get("reason"))}
    def crm_pricing_search(a, b, p): R(a, "crm.admin.pricing.view");  return {"values": masterdata.list_values(_conn, a, "commercial.pricing_policy")}
    def crm_pricing_create(a, b, p): R(a, "crm.admin.pricing.manage"); return {"id": masterdata.create_value(_conn, a, "commercial.pricing_policy", b["code"], b["name"], description=b.get("description"))}

    # ---- Customer numbering ----------------------------------------------
    def crm_num_preview(a, b, p):  R(a, "crm.admin.numbering.manage"); return crm.preview_number(_conn, a, branch=b.get("branch"))
    def crm_num_config(a, b, p):
        R(a, "crm.admin.numbering.manage")
        for k in ("prefix", "suffix", "padding", "include_year", "include_branch", "reset", "enabled"):
            if k in b:
                admin_platform.set_config(_conn, "platform", "", "crm.numbering." + k, str(b[k]), actor=a)
        return crm.preview_number(_conn, a, branch=b.get("branch"))

    # ---- Duplicate detection + merge -------------------------------------
    def crm_dup_rules(a, b, p):    return {"rules": crm.list_duplicate_rules(_conn, a)}
    def crm_dup_rule_add(a, b, p): return {"id": crm.create_duplicate_rule(_conn, a, b["name"], b["dimension"], b.get("match_type", "exact"), b.get("weight", 1.0))}
    def crm_detect(a, b, p):       return crm.detect_duplicates(_conn, a, int(p["id"]))
    def crm_review(a, b, p):       return {"ok": crm.review_candidate(_conn, a, int(p["id"]), b["status"], reason=b.get("reason"))}
    def crm_merge_preview(a, b, p): return crm.merge_preview(_conn, a, int(b["survivor_id"]), int(b["merged_id"]))
    def crm_merge(a, b, p):        return crm.merge_customers(_conn, a, int(b["survivor_id"]), int(b["merged_id"]), reason=b.get("reason"))

    # ---- Credit policy ----------------------------------------------------
    def crm_credit_list(a, b, p):  return {"policies": crm.list_credit_policies(_conn, a)}
    def crm_credit_add(a, b, p):
        return {"id": crm.create_credit_policy(_conn, a, b["code"], b.get("name"), credit_limit=b.get("credit_limit"),
                payment_terms=b.get("payment_terms"), deposit_required_pct=b.get("deposit_required_pct"),
                credit_status=b.get("credit_status", "GOOD"), overdue_restriction=b.get("overdue_restriction", False),
                booking_restriction=b.get("booking_restriction", False), service_suspension=b.get("service_suspension", False),
                effective_from=b.get("effective_from"), effective_to=b.get("effective_to"))}
    def crm_credit_eval(a, b, p):  R(a, "crm.admin.credit_policy.view"); return crm.evaluate_credit(_conn, a, int(p["id"]), b.get("action", "quotation"), amount=b.get("amount", 0), policy_code=b.get("policy_code"))

    # ---- CRM custom fields ------------------------------------------------
    def crm_cf_search(a, b, p):    return {"fields": crm.list_custom_fields(_conn, a, entity=b.get("entity"), include_inactive=b.get("include_inactive", True))}
    def crm_cf_create(a, b, p):
        return {"id": crm.create_custom_field(_conn, a, b["entity"], b["code"], b["label"], b["data_type"],
                required=b.get("required", False), default_value=b.get("default_value"), validation=b.get("validation"),
                selection_source=b.get("selection_source"), visibility=b.get("visibility", "visible"),
                editability=b.get("editability", "editable"), sensitivity=b.get("sensitivity", "normal"),
                searchable=b.get("searchable", False), reportable=b.get("reportable", False),
                exportable=b.get("exportable", True), effective_from=b.get("effective_from"), effective_to=b.get("effective_to"))}
    def crm_cf_status(a, b, p):    return {"ok": crm.set_custom_field_status(_conn, a, int(p["id"]), b["status"])}
    def crm_cf_setval(a, b, p):    return {"ok": crm.set_custom_value(_conn, a, b["entity"], int(b["entity_id"]), b["field_code"], b.get("value"))}
    def crm_cf_getval(a, b, p):    return {"values": crm.get_custom_values(_conn, a, b["entity"], int(b["entity_id"]))}

    return {
        ("GET", "/admin/master-data/domains"): md_domains,
        ("POST", "/admin/master-data/values/search"): md_search,
        ("POST", "/admin/master-data/values"): md_create,
        ("POST", "/admin/master-data/values/:id"): md_update,
        ("POST", "/admin/master-data/values/:id/status"): md_status,
        ("GET", "/admin/master-data/values/:id/dependencies"): md_deps,
        ("POST", "/admin/master-data/values/:id/replace"): md_replace,
        ("POST", "/admin/master-data/import"): md_import,
        ("POST", "/admin/master-data/export"): md_export,
        ("POST", "/admin/crm/classifications/search"): crm_class_search,
        ("POST", "/admin/crm/classifications"): crm_class_create,
        ("POST", "/admin/crm/classifications/:id/status"): crm_class_status,
        ("POST", "/admin/crm/pricing/search"): crm_pricing_search,
        ("POST", "/admin/crm/pricing"): crm_pricing_create,
        ("POST", "/admin/crm/numbering/preview"): crm_num_preview,
        ("POST", "/admin/crm/numbering/config"): crm_num_config,
        ("GET", "/admin/crm/duplicate-rules"): crm_dup_rules,
        ("POST", "/admin/crm/duplicate-rules"): crm_dup_rule_add,
        ("POST", "/admin/crm/customers/:id/detect-duplicates"): crm_detect,
        ("POST", "/admin/crm/duplicate-candidates/:id/review"): crm_review,
        ("POST", "/admin/crm/merge/preview"): crm_merge_preview,
        ("POST", "/admin/crm/merge"): crm_merge,
        ("GET", "/admin/crm/credit-policies"): crm_credit_list,
        ("POST", "/admin/crm/credit-policies"): crm_credit_add,
        ("POST", "/admin/crm/customers/:id/evaluate-credit"): crm_credit_eval,
        ("POST", "/admin/crm/custom-fields/search"): crm_cf_search,
        ("POST", "/admin/crm/custom-fields"): crm_cf_create,
        ("POST", "/admin/crm/custom-fields/:id/status"): crm_cf_status,
        ("POST", "/admin/crm/custom-values"): crm_cf_setval,
        ("POST", "/admin/crm/custom-values/get"): crm_cf_getval,
    }


def _phase4_routes():
    """Phase 4 — Workflow Administration: definitions/versions/designer/validation/simulation/
    publication, instances, approval matrices, SLA, escalation, delegation. Permission-gated,
    tenant-scoped, audited; simulation is non-mutating."""
    import workflow as wf
    import wfgov

    # ---- Definitions & versions ------------------------------------------
    def wf_list(a, b, p):        return {"definitions": wf.list_definitions(_conn, a)}
    def wf_create(a, b, p):      return {"id": wf.create_definition(_conn, a, b["domain"], b["code"], b["name"], description=b.get("description"), org_scope=b.get("org_scope"), risk_level=b.get("risk_level", "medium"))}
    def wf_versions(a, b, p):    return {"versions": wf.list_versions(_conn, a, p["code"])}
    def wf_new_version(a, b, p): return {"id": wf.create_version(_conn, a, p["code"], change_reason=b.get("change_reason"))}
    def wf_ver_get(a, b, p):
        core.require(a, "workflow.definition.view")
        vid = int(p["id"])
        return {"steps": wf.steps(_conn, vid), "transitions": wf.transitions(_conn, vid)}
    def wf_add_step(a, b, p):    return {"id": wf.add_step(_conn, a, int(p["id"]), b["code"], b["step_type"], name=b.get("name"), description=b.get("description"), entry_criteria=b.get("entry_criteria"), assigned_role=b.get("assigned_role"), assigned_org_scope=b.get("assigned_org_scope"), sla_code=b.get("sla_code"), escalation_code=b.get("escalation_code"), notification_rule=b.get("notification_rule"), sort_order=b.get("sort_order", 0))}
    def wf_del_step(a, b, p):    return {"ok": wf.delete_step(_conn, a, int(p["id"]), b["code"])}
    def wf_add_trans(a, b, p):   return {"id": wf.add_transition(_conn, a, int(p["id"]), b["source_step"], b["target_step"], b["action"], required_permission=b.get("required_permission"), required_role=b.get("required_role"), condition=b.get("condition"), approval_required=b.get("approval_required", False), approval_matrix_code=b.get("approval_matrix_code"), reason_required=b.get("reason_required", False), audit_event=b.get("audit_event"), notification=b.get("notification"), sla_effect=b.get("sla_effect"))}
    def wf_validate(a, b, p):    return wf.validate_version(_conn, a, int(p["id"]))
    def wf_simulate(a, b, p):    return wf.simulate(_conn, a, int(p["id"]), b.get("ctx", {}))
    def wf_approve(a, b, p):     return {"ok": wf.approve_version(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def wf_reject(a, b, p):      return {"ok": wf.reject_version(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def wf_publish(a, b, p):     return wf.publish_version(_conn, a, int(p["id"]), b["change_reason"], effective_from=b.get("effective_from"))
    def wf_retire(a, b, p):      return {"ok": wf.retire_version(_conn, a, int(p["id"]), reason=b.get("reason"))}

    # ---- Instances -------------------------------------------------------
    def wf_start(a, b, p):       return {"id": wf.start_instance(_conn, a, b["code"], b["entity_type"], b.get("entity_id"), org_scope=b.get("org_scope"))}
    def wf_inst_list(a, b, p):   return {"instances": wf.list_instances(_conn, a, code=b.get("code"), status=b.get("status"))}
    def wf_inst_get(a, b, p):    return {"instance": wf.get_instance(_conn, a, int(p["id"])), "history": wf.instance_history(_conn, a, int(p["id"])), "escalations": wfgov.escalation_history(_conn, a, int(p["id"]))}
    def wf_advance(a, b, p):     return wf.advance_instance(_conn, a, int(p["id"]), b["action"], ctx=b.get("ctx", {}), reason=b.get("reason"))
    def wf_reassign(a, b, p):    return {"ok": wf.reassign_instance(_conn, a, int(p["id"]), b["user_id"], role=b.get("role"), reason=b.get("reason"))}
    def wf_cancel(a, b, p):      return {"ok": wf.cancel_instance(_conn, a, int(p["id"]), reason=b.get("reason"))}

    # ---- Approval matrices / SLA / escalation / delegation ---------------
    def wf_matrices(a, b, p):    return {"matrices": wfgov.list_matrices(_conn, a)}
    def wf_matrix_add(a, b, p):  return {"id": wfgov.create_matrix(_conn, a, b["code"], b["name"], domain=b.get("domain"), mode=b.get("mode", "single"), allow_self_approval=b.get("allow_self_approval", False))}
    def wf_matrix_rule(a, b, p): return {"id": wfgov.add_matrix_rule(_conn, a, p["code"], b["approver_type"], approver_ref=b.get("approver_ref"), dimension=b.get("dimension"), op=b.get("op", "gte"), value=b.get("value"), seq=b.get("seq", 0), level=b.get("level", 1))}
    def wf_sla_list(a, b, p):
        core.require(a, "workflow.definition.view")
        return {"sla": _rows(_conn.execute("SELECT * FROM sla_rules ORDER BY code").fetchall())}
    def wf_sla_add(a, b, p):     return {"id": wfgov.create_sla(_conn, a, b["code"], b["name"], b["duration_minutes"], sla_type=b.get("sla_type"), working_calendar_ref=b.get("working_calendar_ref"), holiday_calendar_ref=b.get("holiday_calendar_ref"), warning_pct=b.get("warning_pct", 80), escalation_code=b.get("escalation_code"), owner_role=b.get("owner_role"), severity=b.get("severity", "medium"))}
    def wf_sla_due(a, b, p):
        core.require(a, "workflow.definition.view")
        return wfgov.compute_due(_conn, a, b["code"], b.get("start"))
    def wf_esc_list(a, b, p):
        core.require(a, "workflow.definition.view")
        return {"escalations": _rows(_conn.execute("SELECT * FROM escalation_rules ORDER BY code").fetchall())}
    def wf_esc_add(a, b, p):     return {"id": wfgov.create_escalation(_conn, a, b["code"], b["name"], b["target_type"], target_ref=b.get("target_ref"), after_minutes=b.get("after_minutes", 0), severity=b.get("severity", "medium"))}
    def wf_deleg_list(a, b, p):  return {"delegations": wfgov.list_delegations(_conn, a)}
    def wf_deleg_add(a, b, p):   return {"id": wfgov.create_delegation(_conn, a, int(b["delegator"]), int(b["delegate"]), b.get("role"), b.get("domain"), b.get("start_at"), b["end_at"], reason=b.get("reason"))}
    def wf_deleg_revoke(a, b, p): return {"ok": wfgov.revoke_delegation(_conn, a, int(p["id"]), reason=b.get("reason"))}

    return {
        ("GET", "/admin/workflows"): wf_list,
        ("POST", "/admin/workflows"): wf_create,
        ("GET", "/admin/workflows/:code/versions"): wf_versions,
        ("POST", "/admin/workflows/:code/versions"): wf_new_version,
        ("GET", "/admin/workflow-versions/:id"): wf_ver_get,
        ("POST", "/admin/workflow-versions/:id/steps"): wf_add_step,
        ("POST", "/admin/workflow-versions/:id/steps/delete"): wf_del_step,
        ("POST", "/admin/workflow-versions/:id/transitions"): wf_add_trans,
        ("POST", "/admin/workflow-versions/:id/validate"): wf_validate,
        ("POST", "/admin/workflow-versions/:id/simulate"): wf_simulate,
        ("POST", "/admin/workflow-versions/:id/approve"): wf_approve,
        ("POST", "/admin/workflow-versions/:id/reject"): wf_reject,
        ("POST", "/admin/workflow-versions/:id/publish"): wf_publish,
        ("POST", "/admin/workflow-versions/:id/retire"): wf_retire,
        ("POST", "/admin/workflow-instances"): wf_start,
        ("GET", "/admin/workflow-instances"): wf_inst_list,
        ("GET", "/admin/workflow-instances/:id"): wf_inst_get,
        ("POST", "/admin/workflow-instances/:id/advance"): wf_advance,
        ("POST", "/admin/workflow-instances/:id/reassign"): wf_reassign,
        ("POST", "/admin/workflow-instances/:id/cancel"): wf_cancel,
        ("GET", "/admin/workflow/matrices"): wf_matrices,
        ("POST", "/admin/workflow/matrices"): wf_matrix_add,
        ("POST", "/admin/workflow/matrices/:code/rules"): wf_matrix_rule,
        ("GET", "/admin/workflow/sla"): wf_sla_list,
        ("POST", "/admin/workflow/sla"): wf_sla_add,
        ("POST", "/admin/workflow/sla/due"): wf_sla_due,
        ("GET", "/admin/workflow/escalations"): wf_esc_list,
        ("POST", "/admin/workflow/escalations"): wf_esc_add,
        ("GET", "/admin/workflow/delegations"): wf_deleg_list,
        ("POST", "/admin/workflow/delegations"): wf_deleg_add,
        ("POST", "/admin/workflow/delegations/:id/revoke"): wf_deleg_revoke,
    }


def _phase5_routes():
    """Phase 5 — Form & Custom-Field Administration: definitions/versions/designer/sections/fields/
    options/validation/simulation/publication, dependency analysis, and runtime rendering/submission/
    values/search/export/files. Permission-gated, tenant-scoped, audited; simulation non-mutating."""
    import forms

    def f_list(a, b, p):      return {"definitions": forms.list_definitions(_conn, a, entity_type=b.get("entity_type"))}
    def f_create(a, b, p):    return {"id": forms.create_definition(_conn, a, b["entity_type"], b["code"], b["name"], description=b.get("description"), org_scope=b.get("org_scope"))}
    def f_clone(a, b, p):     return {"id": forms.clone_definition(_conn, a, p["code"], b["new_code"], b["new_name"])}
    def f_versions(a, b, p):  return {"versions": forms.list_versions(_conn, a, p["code"])}
    def f_new_ver(a, b, p):   return {"id": forms.create_version(_conn, a, p["code"], change_reason=b.get("change_reason"))}
    def f_ver_get(a, b, p):
        core.require(a, "form.definition.view")
        return {"sections": forms.sections(_conn, int(p["id"])), "fields": forms.fields(_conn, int(p["id"]))}
    def f_add_section(a, b, p): return {"id": forms.add_section(_conn, a, int(p["id"]), b["code"], b.get("title"), sort_order=b.get("sort_order", 0), collapsible=b.get("collapsible", False), default_expanded=b.get("default_expanded", True), visibility=b.get("visibility"), role_restriction=b.get("role_restriction"))}
    def f_add_field(a, b, p): return {"id": forms.add_field(_conn, a, int(p["id"]), b["code"], b["label"], b["data_type"], section_code=b.get("section_code"), required=b.get("required", False), required_condition=b.get("required_condition"), default_value=b.get("default_value"), validation=b.get("validation"), visibility=b.get("visibility"), editability=b.get("editability"), sensitivity=b.get("sensitivity", "INTERNAL"), searchable=b.get("searchable", False), reportable=b.get("reportable", False), exportable=b.get("exportable", True), master_data_domain=b.get("master_data_domain"), role_restriction=b.get("role_restriction"), workflow_stage=b.get("workflow_stage"), display_order=b.get("display_order", 0), options=b.get("options"))}
    def f_del_field(a, b, p): return {"ok": forms.delete_field(_conn, a, int(p["id"]), b["code"])}
    def f_add_option(a, b, p): return {"id": forms.add_option(_conn, a, int(p["id"]), b["code"], label=b.get("label"), sort_order=b.get("sort_order", 0))}
    def f_deact_option(a, b, p): return {"ok": forms.deactivate_option(_conn, a, int(p["id"]), replacement_code=b.get("replacement_code"))}
    def f_validate(a, b, p):  return forms.validate_version(_conn, a, int(p["id"]))
    def f_simulate(a, b, p):  return forms.simulate(_conn, a, int(p["id"]), b.get("ctx", {}), values=b.get("values", {}))
    def f_approve(a, b, p):   return {"ok": forms.approve_version(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def f_reject(a, b, p):    return {"ok": forms.reject_version(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def f_publish(a, b, p):   return forms.publish_version(_conn, a, int(p["id"]), b["change_reason"], effective_from=b.get("effective_from"))
    def f_retire(a, b, p):    return {"ok": forms.retire_version(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def f_deps(a, b, p):      return forms.field_dependencies(_conn, a, int(p["id"]), b["code"])
    def f_migration(a, b, p):
        core.require(a, "form.data.remediate")
        return forms.classify_existing(_conn)

    # ---- runtime rendering / submission / values ----
    def f_effective(a, b, p): return forms.effective_form(_conn, a, b["entity_type"], role=b.get("role"), stage=b.get("stage"), portal=b.get("portal", False))
    def f_submit(a, b, p):    return forms.submit_values(_conn, a, b["entity_type"], int(b["entity_id"]), b.get("values", {}), stage=b.get("stage"))
    def f_get_values(a, b, p): return {"values": forms.get_values(_conn, a, b["entity_type"], int(b["entity_id"]))}
    def f_search(a, b, p):    return {"results": forms.search_values(_conn, a, b["entity_type"], b["field_code"], b["query"])}
    def f_export(a, b, p):    return forms.export_values(_conn, a, b["entity_type"])
    def f_upload(a, b, p):    return forms.upload_file(_conn, a, b["entity_type"], int(b["entity_id"]), b["field_code"], b["filename"], b["content_type"], int(b.get("size_bytes", 0)), allowed_types=b.get("allowed_types"), max_size=b.get("max_size"))
    def f_sign(a, b, p):      return {"id": forms.add_signature(_conn, a, b["entity_type"], int(b["entity_id"]), b["field_code"], b["document_hash"], b["meaning"], form_version=b.get("form_version"), source_meta=b.get("source_meta"))}

    return {
        ("GET", "/admin/forms"): f_list,
        ("POST", "/admin/forms"): f_create,
        ("POST", "/admin/forms/:code/clone"): f_clone,
        ("GET", "/admin/forms/:code/versions"): f_versions,
        ("POST", "/admin/forms/:code/versions"): f_new_ver,
        ("GET", "/admin/form-versions/:id"): f_ver_get,
        ("POST", "/admin/form-versions/:id/sections"): f_add_section,
        ("POST", "/admin/form-versions/:id/fields"): f_add_field,
        ("POST", "/admin/form-versions/:id/fields/delete"): f_del_field,
        ("POST", "/admin/form-fields/:id/options"): f_add_option,
        ("POST", "/admin/form-options/:id/deactivate"): f_deact_option,
        ("POST", "/admin/form-versions/:id/validate"): f_validate,
        ("POST", "/admin/form-versions/:id/simulate"): f_simulate,
        ("POST", "/admin/form-versions/:id/approve"): f_approve,
        ("POST", "/admin/form-versions/:id/reject"): f_reject,
        ("POST", "/admin/form-versions/:id/publish"): f_publish,
        ("POST", "/admin/form-versions/:id/retire"): f_retire,
        ("POST", "/admin/form-versions/:id/dependencies"): f_deps,
        ("GET", "/admin/forms/migration"): f_migration,
        ("POST", "/admin/forms/effective"): f_effective,
        ("POST", "/admin/forms/values"): f_submit,
        ("POST", "/admin/forms/values/get"): f_get_values,
        ("POST", "/admin/forms/search"): f_search,
        ("POST", "/admin/forms/export"): f_export,
        ("POST", "/admin/forms/files"): f_upload,
        ("POST", "/admin/forms/signatures"): f_sign,
    }


def _phase6_routes():
    """Phase 6 — Platform & System Settings: settings/secrets/flags/modules/maintenance/retention/
    backup/restore/branding/templates/integrity. Permission-gated, tenant-scoped, audited; secrets
    masked; security minimums enforced."""
    import settings as s

    def st_defs(a, b, p):     return {"definitions": s.list_definitions(_conn, a, category=b.get("category"))}
    def st_set(a, b, p):      return {"id": s.set_value(_conn, a, b["key"], b["value"], scope=b.get("scope", "platform"), scope_ref=b.get("scope_ref"), effective_from=b.get("effective_from"), effective_to=b.get("effective_to"), reason=b.get("reason"))}
    def st_eff(a, b, p):
        core.require(a, "platform.settings.view")
        return s.effective_value(_conn, a, b["key"], tenant=b.get("tenant"), org_chain=b.get("org_chain"))
    def st_hist(a, b, p):     return {"history": s.value_history(_conn, a, b["key"], scope=b.get("scope"))}
    # secrets
    def sec_list(a, b, p):    return {"secrets": s.list_secret_references(_conn, a)}
    def sec_create(a, b, p):  return {"id": s.create_secret_reference(_conn, a, b["code"], b.get("provider"), b["env_name"], scope=b.get("scope", "platform"), rotation_days=b.get("rotation_days", 90), masked_hint=b.get("masked_hint"))}
    def sec_validate(a, b, p): return s.validate_secret_reference(_conn, a, b["code"])
    def sec_rotate(a, b, p):  return {"ok": s.rotate_secret_reference(_conn, a, b["code"])}
    def sec_revoke(a, b, p):  return {"ok": s.revoke_secret_reference(_conn, a, b["code"])}
    # flags
    def fl_list(a, b, p):     return {"flags": s.list_flags(_conn, a)}
    def fl_create(a, b, p):   return {"id": s.create_flag(_conn, a, b["key"], description=b.get("description"), platform_default=b.get("platform_default", False), dependency=b.get("dependency"), risk=b.get("risk", "low"), expires_at=b.get("expires_at"))}
    def fl_override(a, b, p): return {"ok": s.set_flag_override(_conn, a, p["key"], b["enabled"], tenant=b.get("tenant"), scope=b.get("scope", "tenant"), scope_ref=b.get("scope_ref"), effective_from=b.get("effective_from"), effective_to=b.get("effective_to"))}
    def fl_kill(a, b, p):     return {"ok": s.emergency_disable_flag(_conn, a, p["key"], reason=b.get("reason"))}
    # modules
    def mod_list(a, b, p):    return {"modules": s.list_modules(_conn, a)}
    def mod_impact(a, b, p):  return s.module_disable_impact(_conn, a, p["code"])
    def mod_set(a, b, p):     return {"ok": s.set_module_status(_conn, a, p["code"], b["enabled"], reason=b.get("reason"))}
    # maintenance
    def mt_schedule(a, b, p): return {"id": s.schedule_maintenance(_conn, a, b["mode"], b.get("starts_at"), b["ends_at"], message=b.get("message"), scope=b.get("scope", "tenant"), allowed_roles=b.get("allowed_roles", "admin"))}
    def mt_status(a, b, p):
        core.require(a, "maintenance.view")
        return {"active": s.maintenance_status(_conn, tenant=a.get("tenant_id"))}
    def mt_end(a, b, p):      return {"ok": s.end_maintenance(_conn, a, int(p["id"]), reason=b.get("reason"))}
    # retention
    def ret_list(a, b, p):    return {"policies": s.list_retention(_conn, a)}
    def ret_set(a, b, p):     return {"ok": s.set_retention(_conn, a, b["category"], b["retention_days"], legal_hold=b.get("legal_hold", False), archive_behavior=b.get("archive_behavior", "archive"), deletion_behavior=b.get("deletion_behavior", "soft"), platform_minimum_days=b.get("platform_minimum_days"))}
    # backup / restore
    def bk_list(a, b, p):     return {"backups": s.list_backups(_conn, a)}
    def bk_exec(a, b, p):     return s.execute_backup(_conn, a, kind=b.get("kind", "logical"), storage_ref=b.get("storage_ref"), encryption_ref=b.get("encryption_ref"))
    def rs_request(a, b, p):  return {"id": s.request_restore(_conn, a, int(b["backup_run_id"]), reason=b.get("reason"), target=b.get("target", "isolated"))}
    def rs_validate(a, b, p): return {"ok": s.validate_restore(_conn, a, int(p["id"]))}
    def rs_approve(a, b, p):  return {"ok": s.approve_restore(_conn, a, int(p["id"]), reason=b.get("reason"))}
    # branding / templates
    def br_get(a, b, p):      return {"branding": s.get_branding(_conn, a)}
    def br_set(a, b, p):      return {"ok": s.set_branding(_conn, a, b["kind"], value=b.get("value"), file_ref=b.get("file_ref"), content_type=b.get("content_type"), size_bytes=b.get("size_bytes"))}
    def tpl_list(a, b, p):    return {"templates": s.list_templates(_conn, a)}
    def tpl_create(a, b, p):  return {"id": s.create_template(_conn, a, b["code"], b["name"], b.get("channel"), b["body"], allowed_variables=b.get("allowed_variables"))}
    def tpl_publish(a, b, p): return s.publish_template(_conn, a, int(p["id"]), reason=b.get("reason"))
    # integrity + migration
    def integ(a, b, p):       return s.integrity_checks(_conn, a)
    def migr(a, b, p):
        core.require(a, "platform.settings.view")
        return s.classify_existing(_conn)

    return {
        ("GET", "/admin/settings/definitions"): st_defs,
        ("POST", "/admin/settings/values"): st_set,
        ("POST", "/admin/settings/effective"): st_eff,
        ("POST", "/admin/settings/history"): st_hist,
        ("GET", "/admin/settings/secrets"): sec_list,
        ("POST", "/admin/settings/secrets"): sec_create,
        ("POST", "/admin/settings/secrets/validate"): sec_validate,
        ("POST", "/admin/settings/secrets/rotate"): sec_rotate,
        ("POST", "/admin/settings/secrets/revoke"): sec_revoke,
        ("GET", "/admin/settings/flags"): fl_list,
        ("POST", "/admin/settings/flags"): fl_create,
        ("POST", "/admin/settings/flags/:key/override"): fl_override,
        ("POST", "/admin/settings/flags/:key/kill"): fl_kill,
        ("GET", "/admin/settings/modules"): mod_list,
        ("POST", "/admin/settings/modules/:code/impact"): mod_impact,
        ("POST", "/admin/settings/modules/:code/status"): mod_set,
        ("POST", "/admin/settings/maintenance"): mt_schedule,
        ("GET", "/admin/settings/maintenance"): mt_status,
        ("POST", "/admin/settings/maintenance/:id/end"): mt_end,
        ("GET", "/admin/settings/retention"): ret_list,
        ("POST", "/admin/settings/retention"): ret_set,
        ("GET", "/admin/settings/backups"): bk_list,
        ("POST", "/admin/settings/backups"): bk_exec,
        ("POST", "/admin/settings/restore"): rs_request,
        ("POST", "/admin/settings/restore/:id/validate"): rs_validate,
        ("POST", "/admin/settings/restore/:id/approve"): rs_approve,
        ("GET", "/admin/settings/branding"): br_get,
        ("POST", "/admin/settings/branding"): br_set,
        ("GET", "/admin/settings/templates"): tpl_list,
        ("POST", "/admin/settings/templates"): tpl_create,
        ("POST", "/admin/settings/templates/:id/publish"): tpl_publish,
        ("GET", "/admin/settings/integrity"): integ,
        ("GET", "/admin/settings/migration"): migr,
    }


def _phase7_routes():
    """Phase 7 — Integration Administration + Wise: catalog/profiles/secrets/webhooks/polling/
    reconciliation/dead-letter/replay/health/circuit-breaker + Wise quote/transfer/reconcile/verify/
    refund. Permission-gated, tenant-scoped, audited; secrets masked; provider 200 is never settlement."""
    import integrations as ig
    import wise

    # catalog + profiles
    def cat(a, b, p):        return {"definitions": ig.list_definitions(_conn, a)}
    def prof_list(a, b, p):  return {"profiles": ig.list_profiles(_conn, a, provider_code=b.get("provider_code"))}
    def prof_create(a, b, p): return {"id": ig.create_profile(_conn, a, b["provider_code"], environment=b.get("environment", "MOCK"), name=b.get("name"), secret_ref=b.get("secret_ref"), base_url=b.get("base_url"), default_currency=b.get("default_currency", "PHP"), account_ref=b.get("account_ref"), org_scope=b.get("org_scope"))}
    def prof_validate(a, b, p): return ig.validate_profile(_conn, a, int(p["id"]))
    def prof_activate(a, b, p): return {"ok": ig.activate_profile(_conn, a, int(p["id"]))}
    def prof_suspend(a, b, p): return {"ok": ig.suspend_profile(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def prof_kill(a, b, p):   return {"ok": ig.kill_switch(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def health(a, b, p):      return ig.provider_health(_conn, a, provider_code=b.get("provider_code"))
    # webhooks
    def wh_register(a, b, p): return {"id": ig.register_webhook(_conn, a, b["provider_code"], b["event_type"], secret_ref=b.get("secret_ref"), algorithm=b.get("algorithm", "hmac_sha256"))}
    def wh_events(a, b, p):   return {"events": ig.list_webhook_events(_conn, a, provider_code=b.get("provider_code"))}
    def wh_process(a, b, p):  return ig.process_webhook_event(_conn, a, int(p["id"]))
    # reconciliation
    def rec_list(a, b, p):    return {"items": ig.list_reconciliation(_conn, a, status=b.get("status"))}
    def rec_resolve(a, b, p): return {"ok": ig.resolve_manual_review(_conn, a, int(p["id"]), b["resolution"], reason=b.get("reason"))}
    # dead-letter + replay
    def dlq_list(a, b, p):    return {"items": ig.list_dead_letters(_conn, a, status=b.get("status"))}
    def dlq_replay(a, b, p):  return ig.replay_dead_letter(_conn, a, int(p["id"]), reason=b.get("reason"))
    # fx
    def fx_record(a, b, p):   return {"id": ig.record_fx_rate(_conn, a, b["source_currency"], b["target_currency"], b["rate"], provider_code=b.get("provider_code", "fx_generic"), expiry=b.get("expiry"), manual_override=b.get("manual_override", False))}
    # Wise
    def w_pay(a, b, p):       return wise.create_wise_payment(_conn, a, int(b["booking_id"]), int(b["profile_id"]), b["idem_key"], scenario=b.get("scenario", "completed"))
    def w_transfers(a, b, p): return {"transfers": wise.list_transfers(_conn, a)}
    def w_sync(a, b, p):      return wise.sync_transfer_status(_conn, a, int(p["id"]))
    def w_reconcile(a, b, p): return wise.reconcile_transfer(_conn, a, int(p["id"]))
    def w_verify(a, b, p):    return wise.verify_wise_payment(_conn, a, int(p["id"]), notes=b.get("notes"))
    def w_refund_req(a, b, p): return {"id": wise.request_refund(_conn, a, int(p["id"]), b["amount"], b["reason"])}
    def w_refund_appr(a, b, p): return {"ok": wise.approve_refund(_conn, a, int(p["id"]), reason=b.get("reason"))}
    # webhook ingress (unauthenticated provider callback; verified by signature, not by reaching the URL)
    def w_webhook(a, b, p):
        return ig.ingest_webhook(_conn, "wise", b.get("provider_event_id"), b.get("event_type"), b.get("payload", {}),
                                 signature=b.get("signature"), tenant_id=(a or {}).get("tenant_id"), secret=None)

    return {
        ("GET", "/admin/integrations/catalog"): cat,
        ("GET", "/admin/integrations/profiles"): prof_list,
        ("POST", "/admin/integrations/profiles"): prof_create,
        ("POST", "/admin/integrations/profiles/:id/validate"): prof_validate,
        ("POST", "/admin/integrations/profiles/:id/activate"): prof_activate,
        ("POST", "/admin/integrations/profiles/:id/suspend"): prof_suspend,
        ("POST", "/admin/integrations/profiles/:id/kill"): prof_kill,
        ("GET", "/admin/integrations/health"): health,
        ("POST", "/admin/integrations/webhooks"): wh_register,
        ("GET", "/admin/integrations/webhook-events"): wh_events,
        ("POST", "/admin/integrations/webhook-events/:id/process"): wh_process,
        ("GET", "/admin/integrations/reconciliation"): rec_list,
        ("POST", "/admin/integrations/reconciliation/:id/resolve"): rec_resolve,
        ("GET", "/admin/integrations/dead-letters"): dlq_list,
        ("POST", "/admin/integrations/dead-letters/:id/replay"): dlq_replay,
        ("POST", "/admin/integrations/fx"): fx_record,
        ("POST", "/admin/wise/payments"): w_pay,
        ("GET", "/admin/wise/transfers"): w_transfers,
        ("POST", "/admin/wise/transfers/:id/sync"): w_sync,
        ("POST", "/admin/wise/transfers/:id/reconcile"): w_reconcile,
        ("POST", "/admin/wise/transfers/:id/verify"): w_verify,
        ("POST", "/admin/wise/transfers/:id/refund"): w_refund_req,
        ("POST", "/admin/wise/refunds/:id/approve"): w_refund_appr,
        ("POST", "/admin/wise/webhook"): w_webhook,
    }


def _phase8_routes():
    """Phase 8 — Reporting & Dashboard Administration: datasets/definitions/versions/designer/
    validate/preview/execute/export + KPIs + dashboards + schedules + cache + integrity. Permission-
    gated, tenant-scoped, audited; row + column security enforced; no raw SQL exposed."""
    import reporting as rp

    def ds_list(a, b, p):    return {"datasets": rp.list_datasets(_conn, a)}
    def r_list(a, b, p):     return {"reports": rp.list_reports(_conn, a, category=b.get("category"))}
    def r_create(a, b, p):   return {"id": rp.create_report(_conn, a, b["code"], b["name"], category=b.get("category"), description=b.get("description"), org_scope=b.get("org_scope"))}
    def r_versions(a, b, p): return {"versions": rp.list_versions(_conn, a, p["code"])}
    def r_new_ver(a, b, p):  return {"id": rp.create_version(_conn, a, p["code"], change_reason=b.get("change_reason"))}
    def r_set_spec(a, b, p): return {"ok": rp.set_spec(_conn, a, int(p["id"]), b["spec"])}
    def r_validate(a, b, p): return rp.validate_version(_conn, a, int(p["id"]))
    def r_preview(a, b, p):  return rp.preview(_conn, a, int(p["id"]), target_tenant=b.get("target_tenant"), elevated=b.get("elevated", False))
    def r_approve(a, b, p):  return {"ok": rp.approve_version(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def r_publish(a, b, p):  return rp.publish_version(_conn, a, int(p["id"]), b["change_reason"], effective_from=b.get("effective_from"))
    def r_retire(a, b, p):   return {"ok": rp.retire_version(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def r_run(a, b, p):      return rp.run_report(_conn, a, p["code"], params=b.get("params"), target_tenant=b.get("target_tenant"), elevated=b.get("elevated", False), use_cache=b.get("use_cache", True))
    def r_export(a, b, p):   return rp.export_report(_conn, a, p["code"], fmt=b.get("format", "CSV"), params=b.get("params"))
    def r_exec_hist(a, b, p): return {"executions": rp.execution_history(_conn, a)}
    def r_cache_inv(a, b, p): return {"ok": rp.invalidate_cache(_conn, a, user_id=b.get("user_id"), report_code=b.get("report_code"))}
    def r_integrity(a, b, p): return rp.integrity_checks(_conn, a)
    def r_migration(a, b, p):
        core.require(a, "report.definition.view")
        return rp.classify_existing(_conn)
    # KPIs
    def k_list(a, b, p):     return {"kpis": rp.list_kpis(_conn, a)}
    def k_create(a, b, p):   return {"id": rp.create_kpi(_conn, a, b["code"], b["name"], b["dataset"], b["numerator"], denominator=b.get("denominator"), definition=b.get("definition"), target=b.get("target"), warning=b.get("warning"), critical=b.get("critical"), filters=b.get("filters"))}
    def k_compute(a, b, p):  return rp.compute_kpi(_conn, a, p["code"], target_tenant=b.get("target_tenant"), elevated=b.get("elevated", False))
    # dashboards
    def d_list(a, b, p):     return {"dashboards": rp.list_dashboards(_conn, a)}
    def d_create(a, b, p):   return {"id": rp.create_dashboard(_conn, a, b["code"], b["name"], role_assignment=b.get("role_assignment"))}
    def d_add_widget(a, b, p): return {"id": rp.add_widget(_conn, a, int(p["id"]), b["widget_type"], title=b.get("title"), report_code=b.get("report_code"), kpi_code=b.get("kpi_code"), config=b.get("config"), sort_order=b.get("sort_order", 0))}
    def d_publish(a, b, p):  return {"ok": rp.publish_dashboard(_conn, a, int(p["id"]), reason=b.get("reason"))}
    def d_render(a, b, p):   return rp.render_dashboard(_conn, a, p["code"])
    # schedules
    def s_list(a, b, p):     return {"schedules": rp.list_schedules(_conn, a)}
    def s_create(a, b, p):   return {"id": rp.create_schedule(_conn, a, b["report_code"], b["frequency"], b["recipients"], fmt=b.get("format", "CSV"), channel=b.get("channel", "in_app"), params=b.get("params"))}
    def s_run(a, b, p):      return rp.run_schedule(_conn, a, int(p["id"]))
    def s_deliveries(a, b, p): return {"deliveries": rp.delivery_history(_conn, a)}

    return {
        ("GET", "/admin/reporting/datasets"): ds_list,
        ("GET", "/admin/reporting/reports"): r_list,
        ("POST", "/admin/reporting/reports"): r_create,
        ("GET", "/admin/reporting/reports/:code/versions"): r_versions,
        ("POST", "/admin/reporting/reports/:code/versions"): r_new_ver,
        ("POST", "/admin/reporting/reports/:code/run"): r_run,
        ("POST", "/admin/reporting/reports/:code/export"): r_export,
        ("POST", "/admin/reporting/versions/:id/spec"): r_set_spec,
        ("POST", "/admin/reporting/versions/:id/validate"): r_validate,
        ("POST", "/admin/reporting/versions/:id/preview"): r_preview,
        ("POST", "/admin/reporting/versions/:id/approve"): r_approve,
        ("POST", "/admin/reporting/versions/:id/publish"): r_publish,
        ("POST", "/admin/reporting/versions/:id/retire"): r_retire,
        ("GET", "/admin/reporting/executions"): r_exec_hist,
        ("POST", "/admin/reporting/cache/invalidate"): r_cache_inv,
        ("GET", "/admin/reporting/integrity"): r_integrity,
        ("GET", "/admin/reporting/migration"): r_migration,
        ("GET", "/admin/reporting/kpis"): k_list,
        ("POST", "/admin/reporting/kpis"): k_create,
        ("POST", "/admin/reporting/kpis/:code/compute"): k_compute,
        ("GET", "/admin/reporting/dashboards"): d_list,
        ("POST", "/admin/reporting/dashboards"): d_create,
        ("POST", "/admin/reporting/dashboards/:id/widgets"): d_add_widget,
        ("POST", "/admin/reporting/dashboards/:id/publish"): d_publish,
        ("POST", "/admin/reporting/dashboards/:code/render"): d_render,
        ("GET", "/admin/reporting/schedules"): s_list,
        ("POST", "/admin/reporting/schedules"): s_create,
        ("POST", "/admin/reporting/schedules/:id/run"): s_run,
        ("GET", "/admin/reporting/deliveries"): s_deliveries,
    }


ROUTES = _routes()
ROUTES.update(_ops_routes())
ROUTES.update(_phase2_routes())
ROUTES.update(_admin_routes())
ROUTES.update(_phase3_routes())
ROUTES.update(_phase4_routes())
ROUTES.update(_phase5_routes())
ROUTES.update(_phase6_routes())
ROUTES.update(_phase7_routes())
ROUTES.update(_phase8_routes())


def _match(method, path):
    for (m, tmpl), fn in ROUTES.items():
        if m != method:
            continue
        tp, pp = tmpl.strip("/").split("/"), path.strip("/").split("/")
        if len(tp) != len(pp):
            continue
        params, ok = {}, True
        for a, b in zip(tp, pp):
            if a.startswith(":"):
                params[a[1:]] = b
            elif a != b:
                ok = False; break
        if ok:
            return fn, params
    return None, None


def _cors_origin(req_origin):
    if not CORS_ORIGINS:
        return None
    if "*" in CORS_ORIGINS:
        return "*"
    return req_origin if req_origin in CORS_ORIGINS else None


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origin = _cors_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", getattr(self, "_rid", "-"))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        # structured access log (no bodies/secrets logged)
        dur = round((time.time() - getattr(self, "_t0", time.time())) * 1000, 1)
        log.info("req_id=%s method=%s path=%s status=%s dur_ms=%s",
                 getattr(self, "_rid", "-"), self.command, self.path.split("?")[0], code, dur)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _handle(self, method):
        self._t0 = time.time()
        self._rid = self.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        path = self.path.split("?")[0]
        # unauthenticated liveness/readiness probes
        if method == "GET" and path in ("/health", "/healthz"):
            return self._send(200, {"status": "ok", "env": APP_ENV})
        if method == "GET" and path in ("/ready", "/readyz"):
            try:
                self._conn_ping()
                return self._send(200, {"status": "ready", "schema_version": db.current_version(_conn)})
            except Exception as e:
                return self._send(503, {"status": "not-ready", "detail": str(e)})
        fn, params = _match(method, path)
        if not fn:
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._send(400, {"error": "invalid JSON"})
        try:
            with _DB_LOCK:                      # serialize DB access across worker threads
                core.set_correlation_id(self._rid)   # tag every audited write in this request
                actor = None if self.path == "/login" else _actor(self)
                result = fn(actor, body, params)
            return self._send(200, {"data": result})
        except core.AppError as e:
            return self._send(e.http, {"error": str(e)})
        except KeyError as e:
            return self._send(422, {"error": f"missing field {e}"})
        except Exception as e:  # pragma: no cover
            return self._send(500, {"error": "server error", "detail": str(e)})

    def _conn_ping(self):
        _conn.execute("SELECT 1").fetchone()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, *a):  # handled by _send's structured log
        pass


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)

    def _shutdown(*_):
        log.info("graceful shutdown")
        srv.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except Exception:
        pass
    log.info("RGO OS backend listening on :%d env=%s cors=%s", PORT, APP_ENV, CORS_ORIGINS or "(none)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
