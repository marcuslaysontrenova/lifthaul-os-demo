# LiftHaul — Scaling Architecture (handling concurrent transactions nationwide)

**Question it answers:** how does LiftHaul handle many simultaneous transactions from
Luzon, Visayas and Mindanao?

**First, the honest framing (devil's advocate):**
- **Geography is data, not scale.** Luzon / Visayas / Mindanao are `origin_zone` /
  `dest_zone` values the marketplace already models. They do **not** require geographic
  sharding, regional databases, or per-island services.
- **Concurrency is a runtime problem.** The prior ceiling was architectural: one shared
  DB connection guarded by one global lock (`server._DB_LOCK`) serialized every
  transaction — safe, but one-at-a-time.
- **Right-size it.** Philippine heavy-haul / crane volume is modest (hundreds of bookings
  a day at maturity, not millions). The answer is a connection pool + horizontal scale on
  a managed database — **not** microservices, Kafka, or multi-region DB. Building those
  now would repeat the "infrastructure ahead of demand" anti-pattern.

## What was changed in the code (this repo)

1. **Connection pooling** (`backend/db_pool.py`, wired in `server.py`). When enabled, each
   request checks out its **own** connection and runs **without** the global lock, so
   transactions execute concurrently.
   - PostgreSQL → `psycopg2 ThreadedConnectionPool` (true concurrent transactions).
   - file SQLite → one connection per worker thread (WAL) — dev/verification only.
   - in-memory SQLite → pooling refused (each connection is a separate DB).
   - **Enablement:** explicit opt-in `LIFTHAUL_DB_POOL=1` (default OFF even on Postgres — the
     serialized single-connection mode is the duplicate-free launch default; enabling pooling
     requires the booking-flow atomic hardening in PERFORMANCE_AND_RELIABILITY_PLAN.md §7);
     pool size via `LIFTHAUL_DB_POOL_MAX` (default 10). Default dev path (single conn +
     lock) is unchanged, so the test suite and local runs are unaffected.
2. **Thread-local correlation id** (`core.py`) — concurrent requests no longer overwrite
   each other's audit-trail id.
3. **Statelessness** — sessions are already DB-backed (`sessions` table), so any instance
   can serve any request; no sticky sessions needed.

Proven by `scripts/go_live/concurrency_loadtest.py` (concurrent registrations, zero
failures, distinct carrier per request = correct isolation).

## Production topology (right-sized)

```
                       ┌────────────────────────┐
   Shippers/Carriers   │  Host load balancer     │   (Render/Railway/Fly provides this)
   Luzon·Visayas·      │  TLS, health checks     │
   Mindanao   ───────► │  autoscale on CPU/RPS   │
                       └───────────┬────────────┘
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
              ┌──────────┐  ┌──────────┐  ┌──────────┐   N stateless LiftHaul
              │ app inst │  │ app inst │  │ app inst │   instances (same image),
              │ + pool   │  │ + pool   │  │ + pool   │   LIFTHAUL_DB_POOL=1 (opt-in)
              └────┬─────┘  └────┬─────┘  └────┬─────┘
                   └─────────────┼─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │  Managed PostgreSQL       │  connection limit ≥ Σ(pool_max × instances)
                    │  automated backups + PITR │  read replica later ONLY if measured
                    └──────────────────────────┘
```

**Concurrency math:** effective concurrent transactions ≈ `instances × LIFTHAUL_DB_POOL_MAX`.
Keep `Σ(pool_max × instances)` **below** the managed Postgres connection limit (or put
PgBouncer in front). Start at **2 instances × pool_max 10 = 20 concurrent**; autoscale up
on CPU/RPS. That comfortably covers a national pilot.

## Sequencing (do NOT over-build)

| Stage | Trigger | Action |
|---|---|---|
| **Now** | pre-launch | Single instance + pool on managed Postgres. Enough for pilot. |
| **Scale out** | sustained CPU/RPS or latency SLO breach | Add app instances (stateless — just increase count). |
| **PgBouncer** | connections approach DB limit | Add a pooler in front of Postgres. |
| **Read replica** | read-heavy dashboards measurably strain primary | Add one replica; route reports to it. |
| **Caching (Redis)** | hot config/master-data reads dominate | Add a cache; move the in-process rate-limiter here too. |
| **Regional edge** | genuine cross-island latency complaints | CDN for static; DB stays single-region (PH). |

## Known per-instance state (make shared when you scale out)

- **Public per-IP rate limiter** (`server._public_rate_ok`) and the **API rate limiter**
  (`api_platform._RL_MIN/_RL_DAY`) are in-process — with N instances the effective limit is
  N× the configured value. Acceptable for a pilot; move to Redis/DB when multi-instance
  rate-limit precision matters.

## Verify before claiming capacity

1. `python scripts/go_live/concurrency_loadtest.py --clients 100` against the **hosted
   Postgres** URL with `LIFTHAUL_DB_POOL=1` — expect 0 failures, distinct carrier per request.
2. Watch DB active connections stay under the limit.
3. Add an instance; confirm throughput rises roughly linearly.
