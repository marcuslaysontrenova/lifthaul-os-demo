# LiftHaul — Performance, Scalability & Reliability Plan

Maps the performance/scalability/infrastructure-stability directive to LiftHaul's **actual
state**. It is deliberately honest about what is **proven in the repository today** vs. what
**requires a hosted environment** (owner-provisioned) and therefore cannot be truthfully
tested yet. Fabricated PASS marks are forbidden by the directive itself (§25 "test results
lack evidence" blocks release).

**Legend:** ✅ done/tested in repo · 🟡 partial/tool-ready · ⛔ requires hosted infra (owner) · 📋 documented spec

---

## Executive reality check (devil's advocate)
LiftHaul is **pre-launch, 0 clients, not hosted**. Most of this directive (load at 100–10,000
users, HA DB failover, multi-AZ, CDN/WAF, queues, cache, autoscaling, DR drills, monitoring
stacks) **presupposes cloud infrastructure that does not exist yet** and can only be tested on
a hosted environment. Building all of it now, before one pilot client, is the "infrastructure
ahead of demand" anti-pattern. **The right sequence is: host → pilot → measure → scale.** This
plan therefore *proves* what is repo-controlled, *ships the test tooling*, and *specifies* the
rest for execution once hosted.

## §1 Service-level objectives (targets — 📋 adopt, measure once hosted)
Adopt the directive's initial SLOs (availability ≥99.9%; standard page p95 ≤2s; critical API
p95 ≤1s; booking submit p95 ≤3s; error rate <1%; **zero duplicate financial transactions**;
**zero lost confirmed bookings**; RPO/RTO defined+tested). Report **p50/p95/p99**, never averages.
Status: targets adopted; measurement is ⛔ until hosted (needs real infra + traffic).

## §2 Expected population & capacity forecast (📋 template)
Owner to fill launch / 3-month / 1-year / 3-year + campaign-spike figures (bookers, DAU,
fleet owners, drivers, vehicles, concurrent users, bookings/min, location updates/s,
quotations/min, payments/min, webhooks/s, notifications, uploads, historical growth). Do not
size infra to demo traffic. **This is a business forecast — owner input required.**

## §3–§5 Test levels (baseline→normal→peak→stress→recovery)
🟡 Tooling shipped: `scripts/go_live/concurrency_loadtest.py` (mixed to be extended) drives
staged concurrent load, reports success/shed/hard-fail, isolation, throughput, p50/p95.
⛔ Meaningful numbers require the hosted Postgres environment (local file-SQLite serializes
writers, so local runs prove correctness, not throughput).

## §7 Booking & financial integrity — THE trust-critical part
**✅ Implemented and tested (serially) today.** Existing coverage proves the anti-duplicate /
anti-race invariants at the logic level:

| Invariant | Where enforced | Test evidence |
|---|---|---|
| Idempotent payment ops (same key → one effect) | `mkt_payment_idempotency` UNIQUE(tenant,idem_key) | `test_funding_idempotency`, `test_idempotency_different_payload_rejected` |
| Duplicate payment webhook → single effect | gateway verify + dedupe | `test_verified_webhook_plus_api_check...deduplicates`, `test_duplicate_event` |
| Invalid webhook never mutates payment | signature/verify gate | `test_invalid_webhook_never_changes_payment` |
| Release blocked while dispute open | release gate | `test_active_dispute_blocks`, `test_dispute_freezes_funds` |
| Release requires delivery evidence | `delivery_verification` gate | `test_delivery_required_blocks_early_release` |
| Idempotent release / refund (no double) | state guard | `test_release_idempotent`, `test_refund_idempotency` |
| Self-approval of release/refund denied (SoD) | RBAC/SoD | `test_release_self_approval_denied`, `test_refund_self_approval_denied` |
| Refund cannot exceed balance / disputed cap | ledger guard | `test_refund_exceeds_balance_rejected`, `test_resolution_cannot_exceed_disputed` |
| Carrier cannot select own offer / auto-select off | matching SoD | `test_self_selection_denied`, `test_auto_selection_disabled` |
| Assignment is payment-gated | matching gate | `test_valid_assignment_is_payment_gated` |
| Verify/confirm idempotent (no double job) | `verify_payment`, `confirm_job` idempotency | present in `core.py` + suite |

**Payments are truly idempotent (DB-enforced UNIQUE) → race-safe even under pooling.**

### Known concurrency-hardening backlog (before pooling carries money at scale)
The booking-flow single-winner ops use **read-then-write** guards that are safe under the
**serialized default** but NOT under true parallel writers:
- `marketplace_matching.select_offer` — two parallel selects on one booking.
- `marketplace_matching.create_assignment` — two parallel assignments on one booking.
- `core.confirm_job` — two parallel confirms → double job.
**Fix (feasible, `_Cur.rowcount` supported on both engines):** atomic conditional updates
(`UPDATE … WHERE id=? AND <precondition>` + rowcount check) or UNIQUE(booking_id) constraints,
so exactly one winner commits. **Not launch-blocking** because the default mode serializes.

## Concurrency posture decision (this increment)
- **Launch/pilot default = serialized (single connection + global lock).** Structurally free of
  double-booking/double-assignment/double-payment. ✅ Correct for pilot concurrency.
- **Pooling (`LIFTHAUL_DB_POOL=1`) is an explicit, documented scale-time opt-in** — it lifts the
  throughput ceiling but REQUIRES the atomic hardening above for the booking-flow ops first.
  Pooling no longer auto-enables on Postgres (`db_pool.should_pool` → False by default).
- Backpressure (bounded in-flight → graceful 503) and thread-local correlation id are in place
  for when pooling is enabled. ✅ Proven by `concurrency_loadtest.py` (40/40, 0 hard failures).

## §8–§9 Infrastructure & single-points-of-failure (📋 spec + ⛔ provision)
Target topology (right-sized): host LB + TLS + rate-limit → ≥2 stateless app instances (health
checks, autoscale) → managed Postgres (HA/failover, PITR, pooling/PgBouncer) → managed cache
(sessions/reference/rate-limit) → durable queue (notifications, webhooks, docs, reconciliation)
→ object storage (docs/PODs, signed URLs, scanning). See `SCALING_ARCHITECTURE.md`.
SPOF test matrix (⛔ hosted): lose an instance / AZ / primary DB / cache / queue / storage / any
external provider → app degrades gracefully, never collapses. Repo already: stateless
(DB-backed sessions ✅), durable doc store (`pdfgen.DbStore` ✅), health/ready probes ✅,
fail-closed config ✅.

## §10–§12 Environments, IaC, deployment (📋 + 🟡)
Separate dev/QA/perf/UAT/staging/prod/DR; perf env must mirror prod. IaC (`render.yaml` ✅ is a
start; extend to networks/cache/queue/storage/monitoring). Deployment: rolling/blue-green/canary,
migration validation, readiness gates, auto-rollback, feature flags, backward-compatible
migrations. ⛔ full pipeline needs the hosting platform.

## §18 External-API resilience (📋 + partial)
Timeouts, retries with exponential backoff (never duplicating financial actions), circuit
breakers for payment/maps/SMS/email/verification. Payment retries are idempotency-guarded ✅.
Others to be wired when provider accounts exist (⛔).

## §19 Real-time location scalability (📋)
Do NOT write raw GPS to the booking table. Define update frequency, adaptive throttling
(stationary/poor-signal/no-viewer/high-load/completed), retention, offline buffering, privacy.
High-volume location events → queue/time-series store, not the transactional DB. ⛔ design+build
when live tracking is activated.

## §20–§21 Monitoring, observability & alerting (📋 spec)
Centralize logs/metrics/traces/error-tracking; dashboards tie **technical** (RPS, p95/p99, CPU,
DB connections, slow queries, queue depth) to **business** (booking success %, payment success %,
pending webhooks, failed releases). Critical alerts: payment duplication, wrong release, DB down,
booking loss, unauthorized access, reconciliation mismatch. ⛔ needs the hosted stack.

## §22 Backup & disaster recovery (🟡 tooling + ⛔ drills)
`scripts/go_live/pg_lifecycle.py` + `backup_restore.py` exist. Managed Postgres gives automated
backups + PITR. **A backup is not proven until a restore is tested** — restore drills, RPO/RTO
measurement, and DR exercises are ⛔ until hosted.

## §23–§24 Capacity planning & cost controls (📋)
After each hosted perf test: document safe capacity, headroom, bottleneck, scaling trigger, cost
impact, reassessment date. Autoscaling needs budgets + cost alerts + scaling ceilings.

## §25 Performance release gates
Release is BLOCKED if: workflow SLOs unmet · error rate over threshold · any lost booking or
duplicate transaction · autoscaling/failover/queue-recovery/backup-restore unproven · monitoring
can't see the failure · no evidence. **These gates are adopted; several can only be evaluated on
the hosted environment.**

## §27 Final acceptance — current standing
- ✅ Financial/booking integrity implemented + serially tested (table above).
- ✅ App is stateless / horizontally scalable by design; backpressure + pooling ready.
- ✅ Serialized launch default is structurally duplicate-free.
- ⛔ Horizontal scale under real load, DB failover, DR restore, monitoring, autoscaling — all
  require the hosted environment. **Cannot be marked PASS from the repo. Do not claim otherwise.**

**Bottom line:** integrity is strong and proven at the logic level today; *stability at scale* is
gated on hosting. Host the backend (Step 1 of `GO_LIVE_PLAN.md`), then execute §3–§5, §8–§9,
§13–§16, §22 against it and record results in `PERFORMANCE_TEST_REPORT.md`.
