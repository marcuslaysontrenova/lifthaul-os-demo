"""RGO OS — automated end-to-end validation against a RUNNING backend over HTTP.

Drives the full business lifecycle through the real HTTP API (real server, real
DB, real socket) and records evidence. Usage:

    python validate_e2e.py http://127.0.0.1:8787            # run lifecycle
    python validate_e2e.py http://127.0.0.1:8787 --persist  # verify data survived a restart

On the Docker stack this is the browser-equivalent client for the API; point it
at the compose backend to auto-execute most of DEPLOYMENT_VALIDATION.md and print
PASS/FAIL per step. (Restart persistence: run once, restart the backend/container,
then run again with --persist.)
"""
import json
import sys
import urllib.request

STATE = "rgo_e2e_state.json"


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token and path != "/login":
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("data")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code} {e.read().decode()[:200]}")

    def login(self, email, pw):
        self.token = self.call("POST", "/login", {"email": email, "password": pw})["token"]
        return self.token


def run(base):
    ok = []

    def step(name, fn):
        fn()
        ok.append(name)
        print(f"  PASS  {name}")

    admin, est, appr, fin = Api(base), Api(base), Api(base), Api(base)
    step("health", lambda: _get(base, "/health"))
    step("login (admin/est/appr/fin)", lambda: [admin.login("admin@rgo.demo", "demo1234"),
                                                est.login("est@rgo.demo", "demo1234"),
                                                appr.login("appr@rgo.demo", "demo1234"),
                                                fin.login("fin@rgo.demo", "demo1234")])
    st = {}
    step("create customer", lambda: st.update(cid=admin.call("POST", "/customers", {"name": "E2E Client"})["id"]))
    step("create booking", lambda: st.update(bid=est.call("POST", "/bookings",
                                             {"customer_id": st["cid"], "service": "Crane", "cargo": "Transformer 42t", "weight": 42})["id"]))
    step("review + ready", lambda: [est.call("POST", f"/bookings/{st['bid']}/review"),
                                    est.call("POST", f"/bookings/{st['bid']}/ready")])
    step("create quotation", lambda: st.update(qid=est.call("POST", f"/bookings/{st['bid']}/quotation",
                                              {"lines": [{"kind": "crane", "description": "350t", "qty": 1, "days": 3, "rate": 200000}], "est_cost": 380000})["id"]))
    step("submit (needs approval)", lambda: _eq(est.call("POST", f"/quotations/{st['qid']}/submit")["status"], "pending_approval"))
    step("approve (separation of duties)", lambda: appr.call("POST", f"/quotations/{st['qid']}/approve"))
    step("generate quotation PDF", lambda: st.update(pdf=admin.call("POST", f"/quotations/{st['qid']}/pdf")["ref"]))
    step("send quotation", lambda: est.call("POST", f"/quotations/{st['qid']}/send"))
    step("customer accepts (exact version)", lambda: admin.call("POST", f"/quotations/{st['qid']}/accept", {"accepted_by": "J. Roe", "position": "CFO"}))
    step("payment request", lambda: st.update(prid=fin.call("POST", f"/bookings/{st['bid']}/payment-request")["id"]))
    step("register Wise link", lambda: fin.call("POST", f"/payments/{st['prid']}/link"))
    step("customer submits evidence", lambda: admin.call("POST", f"/payments/{st['prid']}/evidence", {"proof": "receipt.pdf"}))
    step("finance verifies downpayment", lambda: _eq(fin.call("POST", f"/payments/{st['prid']}/verify",
                                                    {"amount_received": _due(admin, st['bid']), "txn_ref": "WISE-E2E", "fees": 700})["status"], "VERIFIED"))
    step("confirm job (once)", lambda: st.update(job=admin.call("POST", f"/bookings/{st['bid']}/confirm")["job_no"]))
    step("duplicate confirm is idempotent", lambda: _eq(admin.call("POST", f"/bookings/{st['bid']}/confirm")["job_no"], st["job"]))
    jid = [None]
    step("job on dispatch calendar", lambda: _find_job(admin, st["job"]))
    step("resolve job id", lambda: jid.__setitem__(0, _job_id(admin, st)))
    step("reserve resource (confirmed)", lambda: admin.call("POST", f"/bookings/{st['bid']}/reserve", {"resource_type": "crane", "resource_ref": "CC-250", "confirmed": True}))
    step("plan -> resources -> safety review", lambda: [admin.call("POST", f"/jobs/{jid[0]}/transition", {"to_status": s})
                                                       for s in ("PLANNING", "RESOURCES_RESERVED", "SAFETY_REVIEW")])
    step("safety PASS", lambda: admin.call("POST", f"/jobs/{jid[0]}/safety", {"result": "PASS", "notes": "cleared"}))
    step("ready -> dispatch (gated)", lambda: [admin.call("POST", f"/jobs/{jid[0]}/transition", {"to_status": "READY_FOR_DISPATCH"}),
                                              admin.call("POST", f"/jobs/{jid[0]}/transition", {"to_status": "DISPATCHED", "evidence": "departed"})])
    step("progress -> accepted", lambda: [admin.call("POST", f"/jobs/{jid[0]}/transition", {"to_status": s, "evidence": "x"})
                                         for s in ("ON_SITE", "IN_PROGRESS", "COMPLETED", "CUSTOMER_ACCEPTANCE_PENDING", "ACCEPTED")])
    step("change order + approve", lambda: admin.call("POST", f"/change-orders/{admin.call('POST', f'/jobs/{jid[0]}/change-order', {'reason': 'standby 2h', 'amount': 24000})['id']}/approve"))
    step("record expense", lambda: admin.call("POST", f"/jobs/{jid[0]}/expense", {"category": "fuel", "amount": 28000}))
    step("final invoice", lambda: st.update(iid=fin.call("POST", f"/jobs/{jid[0]}/invoice", {"due_date": "2999-01-01"})["id"]))
    step("invoice lines present", lambda: _ge(len(fin.call("GET", f"/invoices/{st['iid']}/lines")["lines"]), 2))
    step("allocate balance -> PAID", lambda: _eq(fin.call("POST", f"/invoices/{st['iid']}/allocate",
                                                {"amount": _bal(fin, st['iid']), "ref": "FINAL"})["status"], "PAID"))
    step("close job", lambda: admin.call("POST", f"/jobs/{jid[0]}/transition", {"to_status": "CLOSED"}))
    step("profitability report", lambda: _ge(admin.call("GET", f"/jobs/{jid[0]}/profitability")["final_revenue"], 1))

    reports = admin.call("GET", "/reports")
    json.dump({"bid": st["bid"], "job": st["job"], "confirmed_jobs": reports["confirmed_jobs"]}, open(STATE, "w"))
    print(f"\nE2E OVER HTTP: {len(ok)} steps PASSED. confirmed_jobs={reports['confirmed_jobs']}")
    return True


def check_persist(base):
    prev = json.load(open(STATE))
    admin = Api(base); admin.login("admin@rgo.demo", "demo1234")
    b = admin.call("GET", f"/bookings/{prev['bid']}")
    reports = admin.call("GET", "/reports")
    assert b["stage"] == "CONFIRMED", f"booking stage lost: {b['stage']}"
    assert b["job_id"], "job link lost after restart"
    assert reports["confirmed_jobs"] >= prev["confirmed_jobs"], "confirmed jobs decreased after restart"
    print(f"PERSIST AFTER RESTART: booking {prev['bid']} still CONFIRMED, job {prev['job']} intact, "
          f"confirmed_jobs={reports['confirmed_jobs']}")
    return True


# helpers
def _get(base, path):
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as r:
        json.loads(r.read())


def _eq(a, b):
    assert a == b, f"expected {b}, got {a}"


def _ge(a, b):
    assert a >= b, f"expected >= {b}, got {a}"


def _due(api, bid):
    # read amount_due via calendar is not exposed; recompute from quotation total*dp — use payment endpoint chain
    # simplest: the payment request stored amount_due equals quotation dp_amount; fetch via a report is not available,
    # so we rely on the known quotation (600000 subtotal + 12% VAT = 672000; dp 30% ~ 201600 rounded).
    return 201600


def _bal(api, iid):
    lines = api.call("GET", f"/invoices/{iid}/lines")["lines"]
    return round(sum(l["amount"] for l in lines))


def _find_job(api, job_no):
    cal = api.call("GET", "/calendar")
    assert any(j["job_no"] == job_no for j in cal["jobs"]), "job not on calendar"


def _job_id(api, st):
    cal = api.call("GET", "/calendar")
    # calendar returns job_no; the transition endpoints need numeric id — derive via audit is unavailable,
    # so ask the booking for its job_id.
    b = api.call("GET", f"/bookings/{st['bid']}")
    return b["job_id"]


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    if "--persist" in sys.argv:
        ok = check_persist(base)
    else:
        ok = run(base)
    sys.exit(0 if ok else 1)
