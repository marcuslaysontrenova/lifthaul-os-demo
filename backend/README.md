# RGO OS — Backend (commercial-spine foundation)

A **real, tested backend** for the RGO / LiftHaul commercial spine:
**customer → booking → quotation → approval → acceptance → Wise downpayment →
verification → confirmed job**, with server-side authorization, a relational
database, soft-delete, an audit trail, and a swappable payment provider.

It is deliberately built on the **Python standard library only** (no installs) so
it runs and its tests pass anywhere immediately. It is structured to graduate to
PostgreSQL + FastAPI + a real Wise adapter **without changing the service layer**.

## Run

```bash
python -m unittest test_core -v      # 17 tests — controls + full end-to-end
python server.py                     # HTTP API on http://127.0.0.1:8787
```

Seeded API users (password `demo1234`): `admin@rgo.demo`, `est@rgo.demo`
(estimator), `appr@rgo.demo` (approver), `fin@rgo.demo` (finance).

## What it enforces (server-side — never trust the client)

- **Auth**: pbkdf2 password hashing + bearer-token sessions.
- **RBAC**: least-privilege role → permission map; every service call gated.
- **Controls** (all tested):
  - no quotation **sent** without approval;
  - **separation of duties** — an approver may not approve their own quotation;
  - no **payment request** without an accepted quotation;
  - no **confirmed job** without a **VERIFIED** downpayment;
  - **idempotent** payment verification (no double-processing);
  - **duplicate-job prevention** (one job per booking, transactional);
  - **customer data isolation** (a customer sees only their own records);
  - **quotation versioning** — a sent quote is never overwritten; revisions
    create a new version and supersede the old one;
  - **soft delete + restore** on transactional records;
  - **audit log** on every state change (actor, role, action, entity, old/new).

## Data model (SQLite, real FKs)

`users · sessions · customers · bookings · quotations · quotation_lines ·
payment_requests · jobs · audit_logs` — with `FOREIGN KEY`s, soft-delete columns
(`deleted_by/at`, `deletion_reason`, `restored_by/at`), and created/updated stamps.

## Payment provider (Wise-ready)

`PaymentProvider` is an interface; `MockWiseProvider` is the MVP adapter — it
mints a payment link and reference and **never returns or stores secrets**. A real
`WiseProvider` holds the API key **server-side only** and implements the same two
methods (`create_payment_link`, `get_status`); callers and tests don't change.
Payment is **never assumed** from a returned browser or an uploaded receipt —
finance must verify.

## Honest scope & path to production

**This is the commercial spine, not the whole ERP.** Implemented + tested: the
booking→confirmed-job pipeline and its controls. **Not yet built** (next
increments): site assessment, change orders, actual costing, final billing/
collection, subcontractors/suppliers, inventory, safety, dispatch execution,
notifications/email, file storage + malware scan, reporting, and the remaining
~40 entities from the directive's §23.

To productionize:
1. `core.connect()` → PostgreSQL DSN; port the schema (types are standard).
2. Replace `server.py` with FastAPI/Flask (routes map 1:1); add rate-limiting,
   sessions/refresh, input schemas.
3. Add `WiseProvider` (server-side key in a secret manager) + webhook/reconcile
   via `get_status`.
4. Object storage for attachments (file-type/size validation, AV scan hook).
5. Migrations (Alembic), backups, and CI running `test_core` + new suites.

Deployment: run as a service behind TLS; **secrets never in the frontend**.
```
```
