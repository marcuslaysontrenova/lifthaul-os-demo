# LiftHaul — Performance Test Report (§26)

**Cycle:** pre-hosting baseline · **Date:** 2026-09-06 · **Author:** Enterprise Product Orchestrator (AI)
**Honesty rule:** results are marked REPO-PROVEN (verifiable now) or PENDING-HOSTING (cannot be
truthfully measured without a hosted environment). No fabricated numbers.

1. **Executive summary** — Booking & financial *integrity* is implemented and tested at the logic
   level (idempotent payments, dispute-blocks-release, refund caps, SoD, webhook dedupe). The app
   is stateless and horizontally scalable by design, with connection-pool concurrency + graceful
   backpressure available behind an explicit opt-in. *Stability at scale* (load, failover, DR,
   autoscaling, monitoring) is **not yet measurable** — the backend is not hosted. Launch default
   is the serialized mode, which is structurally free of duplicate bookings/payments.
2. **Infrastructure diagram** — see `SCALING_ARCHITECTURE.md` (target) — PENDING-HOSTING to build.
3. **Test-environment spec** — REPO-PROVEN runs: local, file-SQLite/in-memory, stdlib server.
   PENDING-HOSTING: prod-parity env (managed Postgres, LB, cache, queue, storage).
4. **Workload model** — defined in `PERFORMANCE_AND_RELIABILITY_PLAN.md` §4 (mixed activity mix).
   Executed model so far: concurrent provider registration (write-heavy).
5. **Concurrent-user levels** — REPO-PROVEN: 40 concurrent (local). PENDING-HOSTING: 100→10,000.
6. **Transaction volumes** — PENDING-HOSTING (tie to owner forecast §2).
7. **Response-time percentiles** — REPO-PROVEN (local SQLite, 40 clients): p50 ≈1.6–1.8s,
   p95 ≈2.9–3.5s — *file-SQLite write-serialization artifact, NOT production numbers.*
   PENDING-HOSTING: real p50/p95/p99 on Postgres.
8. **Throughput** — REPO-PROVEN local ≈11–13 req/s (SQLite-bound). PENDING-HOSTING: real.
9. **Error rates** — REPO-PROVEN: **0 hard failures** at 40 concurrent (served or gracefully
   shed as 503). PENDING-HOSTING at higher levels.
10. **Resource utilization** — PENDING-HOSTING (needs infra metrics).
11. **Database findings** — REPO-PROVEN: schema self-applies; pooling (ThreadedConnectionPool)
    available; per-request isolation verified (40/40 distinct carriers). PENDING-HOSTING:
    connection limits, locks, slow queries, failover.
12. **Queue & cache findings** — PENDING-HOSTING (not yet provisioned; specced in plan §8).
13. **External-API findings** — PENDING-INTEGRATION (no live provider accounts; payment/SMS/maps/
    LTFRB not connected). Payment retries are idempotency-guarded in code.
14. **Financial-integrity results** — **REPO-PROVEN (strongest section).** ~30 serial tests pass:
    idempotency, duplicate-webhook dedupe, dispute-blocks-release, delivery-gated release,
    idempotent release/refund, SoD self-approval denied, refund caps. See plan §7 table.
    PENDING-HOSTING: same invariants under true parallel Postgres writers + booking-flow atomic
    hardening (select_offer/create_assignment/confirm_job).
15. **Breaking point** — PENDING-HOSTING (local SQLite serializes; not representative).
16. **Recovery results** — REPO-PROVEN: server returns to normal after a 40-client burst, no
    stuck/duplicate records. PENDING-HOSTING: overload + failover recovery at scale.
17. **Bottleneck analysis** — REPO-PROVEN: at pilot scale the single-connection global lock is the
    throughput ceiling (by design, for safety); file-SQLite write serialization dominates local
    numbers. Lift via pooling + Postgres + horizontal instances (plan §8).
18. **Capacity recommendation** — Start: 1 instance + serialized mode on managed Postgres for the
    pilot (duplicate-free). Scale: enable pooling (after booking-flow hardening) + add stateless
    instances; concurrent ≈ instances × pool_max under the DB connection limit.
19. **Infrastructure-cost estimate** — PENDING (depends on chosen plans; free tier for pilot,
    paid starter for real pilot per `render.yaml` notes).
20. **Defect & remediation register** —
    - D1 (open, non-blocking): booking-flow read-then-write races under pooling
      (select_offer/create_assignment/confirm_job). Mitigation: serialized default + explicit
      pooling opt-in. Fix: atomic single-winner guards. Owner impact: none at pilot scale.
    - D2 (owner): external integrations + hosting absent → live-scale untested.
21. **Go/No-Go recommendation** — **GO for a controlled pilot in serialized mode on managed
    Postgres** (integrity proven, duplicate-free). **NO-GO for high-concurrency scale** until:
    hosted load tests meet SLOs, failover + DR restore proven, booking-flow atomic hardening
    landed, and monitoring is live. Sequence: host → pilot → measure → harden → scale.

## What was executed this cycle (repo-controlled)
- Made pooling an explicit opt-in; serialized (duplicate-free) is the launch default even on Postgres.
- Verified backpressure + isolation: `concurrency_loadtest.py` → 40/40, 0 hard failures.
- Cited/relied on the existing ~30-test financial-integrity suite (serial).
- Authored this report + `PERFORMANCE_AND_RELIABILITY_PLAN.md` (27-section mapping) +
  `SCALING_ARCHITECTURE.md` + `GO_LIVE_PLAN.md`.
- Full backend regression: see commit message for exact pass count.
