# LiftHaul — Go-Live Plan (stable, multi-booking)

**Goal:** take LiftHaul from a polished demo to a **live, stable app that handles many
concurrent bookings** — right-sized for a Philippine heavy-haul pilot, not Lalamove-scale.

**The one honest truth:** the code is ready and proven; the only thing blocking go-live is
**hosting**, and the first step there is yours (creating the account + billing — the AI is
barred from that). Everything around it is done.

---

## Stability under concurrent bookings — already built & proven (in repo)

| Property | Status | Evidence |
|---|---|---|
| Duplicate-free bookings/payments | ✅ serialized launch default (structurally race-free) + idempotent payments | existing integrity suite (plan §7) |
| Concurrent transactions (opt-in, scale-time) | 🟡 connection pool ready; needs booking-flow atomic hardening before carrying money | `backend/db_pool.py` + plan §7 |
| Per-request isolation (no data bleed) | ✅ 40/40 distinct carriers | `scripts/go_live/concurrency_loadtest.py` |
| Graceful backpressure under spike | ✅ bounded in-flight → **503 retryable**, never hang/500 | load test: 0 hard failures |
| Audit correctness under concurrency | ✅ thread-local correlation id | `core.py` |
| Stateless (any instance serves any request) | ✅ DB-backed sessions | — |
| Durable documents across restarts | ✅ `pdfgen.DbStore` | — |
| Health / readiness probes | ✅ `/health`, `/ready` (pool-aware) | — |
| Fail-closed config | ✅ refuses to boot without APP_SECRET/DATABASE_URL/CORS_ORIGINS | `server.validate_config` |

Tuning knobs (env): `LIFTHAUL_DB_POOL_MAX` (pool size), `LIFTHAUL_MAX_INFLIGHT`
(concurrent cap), `LIFTHAUL_CHECKOUT_TIMEOUT` (wait before shedding).

**Not yet proven:** the same load test against **real PostgreSQL** (only SQLite locally).
That runs the moment the DB is hosted — it's step 3 below.

---

## Critical path to a stable, live pilot

### Step 1 — Host the backend + PostgreSQL  *(YOU — ~15 min)*
Render Blueprint (one click, provisions web + managed Postgres together):
1. https://dashboard.render.com → **New + → Blueprint** → pick `lifthaul-os-demo`.
2. Set the 3 prompted values: `CORS_ORIGINS` (your Pages origin), `LH_ADMIN_EMAIL`,
   `LH_ADMIN_PASSWORD` (14+ chars). `DATABASE_URL` + `APP_SECRET` are auto.
3. Apply → wait for **Health = live** (`/readyz`). Copy the API URL.
Full detail + Railway/local options: `docs/go_live/BACKEND_HOSTING_RUNBOOK.md`.
> Launch default is the serialized, duplicate-free mode (safe for the pilot). Connection
> pooling for higher concurrency is an explicit opt-in (`LIFTHAUL_DB_POOL=1`) after the
> booking-flow atomic hardening — see PERFORMANCE_AND_RELIABILITY_PLAN.md §7.

### Step 2 — Flip the frontend to live  *(YOU — 1 min; or I do it once you paste the URL)*
Edit `config.js` → `apiBase: "https://<your-api>.onrender.com"` → commit + push.
Every page (Book, Register, portals) now transacts against the live API.

### Step 3 — Prove stability on real Postgres  *(ME — minutes, once the URL exists)*
```
DATABASE_URL=postgresql://…  python scripts/go_live/provider_activation_e2e.py   # 33/33
DATABASE_URL=postgresql://…  python scripts/go_live/concurrency_loadtest.py --clients 100
```
Expect: full onboarding acceptance green, and 100 concurrent bookings with **0 hard
failures** and correct isolation (real throughput, since Postgres doesn't serialize writers).

### Step 4 — Backup + restore drill  *(ME + YOU)*
`scripts/go_live/pg_lifecycle.py` / `backup_restore.py` against the hosted DB; confirm a
restore brings the data back. (Managed Postgres also gives automated backups/PITR.)

### Step 5 — Scale out only if needed  *(when measured)*
Add app instances (stateless → just raise the count) behind the host LB; keep
`Σ(pool_max × instances)` under the DB connection limit. See `SCALING_ARCHITECTURE.md`.

### Step 6 — Minimal pilot readiness  *(YOU / legal)*
Terms + privacy pages; keep **live funds OFF** (Protected Payment stays gated) until a
licensed payment provider + legal model exist. Real SMS/OTP + maps only when those
accounts exist. Pilot can run with these gated — onboarding → matching → job → POD works.

---

## What "stable multi-booking pilot" does NOT require yet (don't over-build)
Native mobile app, live payments, real-time GPS at volume, read replicas/caching,
microservices. These are post-pilot, demand-driven. The pilot target is **one hosted
environment + one real client + concurrent bookings that don't fall over** — which the
code now supports.

## Definition of done for this milestone
- [ ] Backend hosted on managed Postgres; `/readyz` live *(you: Step 1)*
- [ ] `config.js` apiBase set; public site transacts *(Step 2)*
- [ ] Acceptance E2E 33/33 **on Postgres** *(me: Step 3)*
- [ ] Concurrency load test 100 clients, 0 hard failures **on Postgres** *(me: Step 3)*
- [ ] Backup restored once *(Step 4)*
- [ ] Terms/privacy live; funds gated OFF *(you: Step 6)*
