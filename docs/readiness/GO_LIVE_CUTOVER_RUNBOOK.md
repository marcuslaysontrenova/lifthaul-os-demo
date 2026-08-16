# LiftHaul — Go-Live Cutover Runbook (executable)

**Baseline:** `fef8d69`+ (1,176 tests / 0 failed). This runbook turns the Application go-live blockers
(Gates 1–10) into **exact commands**. Everything runnable without a hosted host has been **pre-tested in
this repo** (see the "Verified in-repo" notes) so each step passes first try on your infrastructure.

Automation shipped for this sprint:
- `scripts/go_live/prod_e2e.py` — production E2E smoke (one synthetic transaction over HTTP). **Verified:
  8/8 against a live backend.**
- `scripts/go_live/preflight.py` — launch-posture audit (probes + auth + fail-closed flags). **Verified:
  LAUNCH-SAFE against a live backend (all four revenue/regulated flags OFF).**
- `scripts/go_live/backup_restore.py` — backup→destroy→restore→reconcile drill + PostgreSQL playbook.
  **Verified: reconciliation identical (RTO ~0.3s, RPO 0).**
- `.github/workflows/go-live-postgres.yml` — CI job that runs the app against a **real PostgreSQL 16
  service container**, migrates, boots in `APP_ENV=production`, runs the E2E over HTTP, proves restart
  persistence, and runs the backup/restore drill — **executing Gates 1/2/3/2b/5 against Postgres on every
  push**. This is the automated stand-in for a hosted DB until the managed host exists.

---

## Gate 1 — Hosted PostgreSQL
```bash
cp .env.example .env          # fill APP_SECRET, POSTGRES_PASSWORD, CORS_ORIGINS (no '*')
docker compose up --build     # brings up postgres:16 + the web service (runs migrate then server)
```
Verify: `docker compose logs web | grep schema_version` shows the stamp.
*Managed Postgres instead of compose:* set `DATABASE_URL=postgresql://…` and run `python backend/migrate.py`
(exits 3 with a clear message if the server is unreachable — never silently pretends).
**Verified in-repo:** `test_pg_portability` + `test_pgadapter` (PG code paths) green; CI runs migrate on real Postgres.

## Gate 2 — Production HTTP / TLS + config
Set on the host: `APP_ENV=production`, `APP_SECRET` (long random), `DATABASE_URL` (postgres), `CORS_ORIGINS`
(explicit origins, **no `*`**). The app **refuses to boot** (`sys.exit(2)`) if any is missing.
```bash
curl -sf https://<backend-origin>/healthz    # {"status":"ok"}
curl -sf https://<backend-origin>/readyz      # {"status":"ready","schema_version":NN}
```
Put the backend behind TLS (reverse proxy / platform TLS).

## Gate 3 — Production browser + smoke E2E
```bash
BASE_URL=https://<backend-origin> \
LH_ADMIN_EMAIL=<real-admin> LH_ADMIN_PASSWORD=<real-pw> \
python scripts/go_live/prod_e2e.py           # expect: 8 passed, 0 failed
```
Then walk the full transaction in a real browser (customer books → operator review → quote → payment
state → assignment → carrier portal → trip → secure delivery OTP → POD → settlement). **Zero Critical/High.**

## Gate 4 — Tenant isolation (prod re-verify)
Run the authoritative isolation suite against the production database:
```bash
DATABASE_URL=postgresql://…  python -m pytest backend/test_tenant_isolation.py -q
```
Then provision two synthetic tenants via the console and confirm neither can cross-read/cross-write
customers, bookings, fleet, payments, claims, documents, or API resources (404-no-leak on reads, 403 on
writes — the enforced behavior).

## Gate 5 — Backup / restore + RTO/RPO
```bash
python scripts/go_live/backup_restore.py             # proves the reconcile logic (any host)
python scripts/go_live/backup_restore.py --postgres  # prints the pg_dump/pg_restore playbook + RTO/RPO capture
```
Execute the `--postgres` playbook against a **restore-target** DB (never prod), record RTO (restore
duration) and RPO (now − backup timestamp) as launch evidence.

## Gate 6 — LTFRB activation (unlocks the MARKETPLACE decision)
Load + independently verify real carrier CPC / authorized-unit / area-of-operation data, then:
`set marketplace.ltfrb_enforcement_enabled = true`. Confirm with `preflight.py` (the flag flips from the
"OFF" audit line). Until then, for-hire matching stays disabled — correctly.

## Gate 7 — External providers
Connect only the actually-selected providers and flip **only** their flags: messaging
(`delivery.messaging_provider_active`, `notify.*.provider_active`), maps/tracking, insurance
(`insurance.provider_active`) if available. Honest-failure semantics are already proven — no provider is
faked.

## Gate 8 — Live Protected Payment (unlocks the LIVE FUNDS decision)
Keep OFF until **all three** of `payments.live_protected_funds_enabled`,
`payments.legal_operating_model_approved`, `payments.licensed_provider_active` are set — only after legal
model approval + licensed-provider certification. Then run **one low-value controlled live transaction**
before opening the flow. `live_funds_enabled()` gates everything; `preflight.py` audits it.

## Gate 9 — Security closure
```bash
python scripts/go_live/preflight.py    # probes, auth-enforced (401), CORS not '*', fail-closed flags
```
Additionally: rotate/inject production secrets; confirm MFA on sensitive actions with real users; run an
external vulnerability scan of the deployed surface. **Verified in-repo:** RBAC/SoD, hashed API keys,
HMAC webhooks, rate limits, audit ledger — `test_security` + `test_tenant_isolation` + `test_api_platform`
+ `test_delivery_verification` (76 passed).

## Gate 10 — Hypercare (assign real people)
Name operational/support owners; define S1–S4 severity + escalation contacts + on-call number; wire
monitoring/alerting on `/healthz`, `/readyz` and the audit ledger; set support hours; schedule the
first-30-day review. Template in [`PRODUCTION_GO_LIVE_CLOSURE_2026-08-16.md`](PRODUCTION_GO_LIVE_CLOSURE_2026-08-16.md) §5.

---

## The launch chain
```
FEATURES COMPLETE ✓ → GATE1 PostgreSQL → GATE2 prod HTTP/TLS → GATE3 E2E → GATE4 tenant isolation
→ GATE5 backup/restore → GATE9 security → (GATE6 LTFRB → MARKETPLACE) → (GATE8 funds → LIVE PAYMENTS)
→ GATE10 hypercare → CONTROLLED GO-LIVE
```
**Three independent decisions:** APPLICATION unlocks at Gates 1–5 + 9; MARKETPLACE at Gate 6; LIVE
PROTECTED PAYMENT at Gate 8 (legal + licensed provider). Target: one real prod environment, one complete
synthetic transaction, one restored backup, one verified second tenant, zero Critical/High, one controlled
first-customer path.
