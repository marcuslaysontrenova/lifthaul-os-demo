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
        core.require(actor, "booking.read")
        return {"quotation_conversion": ops.report_quotation_conversion(_conn),
                "receivables": ops.report_receivables(_conn),
                "confirmed_jobs": ops.report_confirmed_jobs(_conn),
                "awaiting_payment": ops.report_accepted_awaiting_payment(_conn)}

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
    def permissions(a, b, p):    R(a, "role_admin.view");   return {"permissions": _rows(_conn.execute("SELECT code,module,action,description FROM admin_permissions ORDER BY module,action").fetchall())}
    def assignments(a, b, p):    R(a, "user_admin.view");   return {"assignments": _rows(org.user_assignments(_conn, int(p["id"])))}
    def assign(a, b, p):         R(a, "user_admin.manage"); return {"id": org.assign_user(_conn, a, _tid(), int(p["id"]), b["scope_kind"], b["scope_id"], b.get("assignment_type", "PRIMARY"), reason=b.get("reason"))}
    def sessions(a, b, p):       R(a, "security.view");     return {"sessions": _rows(admin_platform.list_sessions(_conn))}
    def session_revoke(a, b, p): R(a, "security.manage");   admin_platform.revoke_session(_conn, b["token"], actor=a); return {"ok": True}
    def login_history(a, b, p):  R(a, "security.view");     return {"history": _rows(admin_platform.list_login_history(_conn, limit=b.get("limit", 100)))}
    def mfa_status(a, b, p):     R(a, "user_admin.view");   return {"enrolled": admin_platform.mfa_enrolled(_conn, int(p["id"]))}

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
        R(a, "system_config.view")
        return org.resolve_org_config(_conn, b["key"], tenant=b.get("tenant"), business_unit=b.get("business_unit"),
                                      branch=b.get("branch"), department=b.get("department"), team=b.get("team"), user=b.get("user"))
    def cfg_list(a, b, p):       R(a, "system_config.view");   return {"config": _rows(_conn.execute("SELECT scope,scope_ref,key,value,effective_to,updated_at FROM platform_config ORDER BY scope,key").fetchall())}
    def cfg_set(a, b, p):        R(a, "system_config.manage"); admin_platform.set_config(_conn, b["scope"], b.get("scope_ref", ""), b["key"], b["value"], actor=a, effective_to=b.get("effective_to")); return {"ok": True}

    # ---- Governance -------------------------------------------------------
    def audit_trail(a, b, p):
        R(a, "audit.view")
        return {"audit": _rows(_conn.execute(
            "SELECT ts,actor,role,action,entity,entity_id,reason,correlation_id FROM audit_logs"
            " ORDER BY id DESC LIMIT ?", (b.get("limit", 100),)).fetchall())}
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
    def data_integrity(a, b, p):
        R(a, "audit.view")
        orphan_assign = _conn.execute(
            "SELECT COUNT(*) c FROM user_organization_assignments ua"
            " LEFT JOIN users u ON u.id=ua.user_id WHERE u.id IS NULL").fetchone()["c"]
        orphan_units = _conn.execute(
            "SELECT COUNT(*) c FROM org_units o WHERE o.parent_id IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM org_units p WHERE p.id=o.parent_id)").fetchone()["c"]
        return {"checks": {"orphan_user_assignments": orphan_assign, "orphan_org_units": orphan_units},
                "ok": orphan_assign == 0 and orphan_units == 0}

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
        ("GET", "/admin/users/:id/assignments"): assignments,
        ("POST", "/admin/users/:id/assignments"): assign,
        ("GET", "/admin/sessions"): sessions,
        ("POST", "/admin/sessions/revoke"): session_revoke,
        ("GET", "/admin/login-history"): login_history,
        ("GET", "/admin/users/:id/mfa"): mfa_status,
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
        ("POST", "/admin/config"): cfg_set,
        ("GET", "/admin/audit"): audit_trail,
        ("GET", "/admin/governance/backfill-status"): backfill_status,
        ("GET", "/admin/governance/backfill-analyze"): backfill_analyze,
        ("POST", "/admin/governance/backfill-dry-run"): backfill_dry_run,
        ("POST", "/admin/governance/backfill-execute"): backfill_execute,
        ("GET", "/admin/governance/backfill-remediation"): backfill_remediation,
        ("GET", "/admin/governance/data-integrity"): data_integrity,
    }


ROUTES = _routes()
ROUTES.update(_ops_routes())
ROUTES.update(_phase2_routes())
ROUTES.update(_admin_routes())


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
