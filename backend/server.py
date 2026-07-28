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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import core
import ops
import admin
import catalog   # noqa: F401  (ensures full schema/roles registered)
import pdfgen
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
_conn = db.connect(os.environ.get("DATABASE_URL"))   # sqlite (dev) or postgres (prod)
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
    return core.actor_for(_conn, auth[7:])


# route table: (METHOD, path) -> handler(actor, body, params)
def _routes():
    def login(actor, body, _):
        return {"token": core.login(_conn, body["email"], body["password"])}

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
        core.require(actor, "job.read"); return ops.job_profitability(_conn, int(p["id"]))

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
        return {"lines": ops.invoice_lines(_conn, int(p["id"]))}

    return {
        ("POST", "/quotations/:id/pdf"): pdf_gen,
        ("GET", "/quotations/:id/pdf"): pdf_get,
        ("GET", "/calendar"): calendar,
        ("GET", "/invoices/:id/lines"): inv_lines,
    }


ROUTES = _routes()
ROUTES.update(_ops_routes())
ROUTES.update(_phase2_routes())


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
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _handle(self, method):
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
            actor = None if self.path == "/login" else _actor(self)
            return self._send(200, {"data": fn(actor, body, params)})
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

    def log_message(self, fmt, *a):  # structured access log
        log.info("%s %s", self.command, self.path)


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
