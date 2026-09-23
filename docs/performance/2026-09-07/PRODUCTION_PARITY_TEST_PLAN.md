# LiftHaul production-parity performance test plan

Status: ready for execution after the target environment and synthetic fixtures exist.

## Entry criteria

- Isolated performance environment matches the production runtime, PostgreSQL engine, pooler, cache,
  queue, worker, object storage, network policy, secrets model, and autoscaling configuration.
- At least two API instances are active in distinct failure domains.
- Synthetic data represents the approved launch/one-year/three-year history sizes.
- Payment, map, SMS, email, identity, and document providers use sandboxes or fault proxies.
- Synthetic booker/provider/fleet/driver/staff accounts and scoped API keys are loaded.
- Live customer data and live payment credentials are prohibited.
- Metrics, logs, traces, business counters, and provider telemetry are retained under one test run ID.
- A stop authority, incident channel, on-call owners, cost ceiling, and environment rollback exist.

## Execution sequence

| Phase | Workload | Duration | Required result |
|---|---|---:|---|
| Baseline | One user completes every critical journey | 10+ min | Correct records and dependency timings |
| Normal | 100 then 500 mixed users | 30 min each | SLOs and all integrity checks pass twice |
| Peak | 1,000 then 2,500 mixed users | 60 min each | SLOs, autoscaling, DB/queue/cache healthy |
| Expansion | 5,000 then 10,000 mixed users | 60 min each | Approved forecast supported |
| Spike | 100→5,000 in one minute | 15 min + recovery | Controlled scaling/backpressure; no loss |
| Stress | Increase beyond approved forecast | Until stable violation | Safe capacity and first bottleneck identified |
| Recovery | Return to 100 users | 60 min | Queues drain and SLOs/data recover |
| Soak | Approved peak mix | 8–24 hours | No leak, drift, buildup, expiry, or stuck state |
| Mature data | Repeat normal and report work | 60 min | Searches/dashboards/reconciliation meet SLO |

The test operator should execute `performance/k6/lifthaul-mixed.js` from distributed load generators.
`ALLOW_SYNTHETIC_MUTATIONS=true` is required to prevent accidental mutation of an unconfirmed target.

## Mandatory race scenarios

Run every race at least 1,000 times across two or more API instances on PostgreSQL:

1. Two providers accept one booking.
2. Two dispatchers assign one vehicle.
3. Client repeats Pay with the same and different idempotency keys.
4. Identical and conflicting payment webhooks arrive concurrently.
5. Payment confirmation arrives after cancellation/expiry.
6. Delivery confirmation races with a finance hold.
7. Payment release races with dispute creation.
8. Two staff users resolve one dispute.
9. Driver submits identical POD twice.
10. Multiple full/partial refund commands arrive concurrently.
11. Network timeout causes client retry after the server committed.
12. Vehicle/driver compliance expires during dispatch.

Each race passes only when the database and external provider reconcile to one legal outcome. HTTP
success alone is insufficient.

## Failure injection matrix

| Injection | When | Expected behavior | Evidence |
|---|---|---|---|
| Terminate one API instance | Mid-booking and mid-read | Healthy instance continues; retry is idempotent | LB metrics, trace, record hash |
| Database failover | Booking/payment bursts | Bounded errors, no false success, recovery within RTO | DB events, client result, ledger reconciliation |
| Cache unavailable | Login/rate/reference load | Controlled degradation; money/status stay authoritative | Circuit state, error budget, state comparison |
| Queue unavailable/backlogged | Notification/webhook load | Command retained or honestly rejected; no event loss | Outbox/queue counts, oldest age, replay |
| Object storage timeout | POD/document upload | Metadata not falsely complete; safe retry | Object hash, DB state, scan status |
| Payment timeout/429/5xx | Checkout and callback | Pending/retry/reconcile; no duplicate charge | Provider plus internal IDs |
| Map timeout/wrong route | Quote request | Draft/address retained; no fabricated route/price | UI/API response, audit record |
| SMS/email failure | Confirmation burst | Booking persists; communication retries | Queue/dead-letter evidence |
| Deploy/restart | Active payment and trip | Backward-compatible state; rollback works | release timeline and migration logs |
| Region loss | DR drill | Restore/failover within RTO/RPO | timestamps, hashes, reconciliation sign-off |

## Post-run reconciliation

For the test run ID, compare expected commands, HTTP results, authoritative database records, durable
events, provider records, and customer-visible states. The result must prove:

- one booking per logical booking command and stable response schema on replay;
- at most one accepted provider and one active vehicle/driver assignment;
- one provider transaction per intended payment and correct amount/currency/reference;
- one financial effect per authenticated webhook, release, refund, or payout;
- protected-payment journal debits equal credits and never include excluded charges;
- dashboards/notifications/invoices agree with canonical records;
- no acknowledged outbox/queue event is lost and dead letters are owned;
- all temporary holds, pending commands, locks, and queues return to an explainable terminal state.

## Evidence package

Every run must archive the test definition and commit, environment/IaC revision, sanitized fixture
manifest, stage timeline, k6 raw summary, p50/p95/p99, errors, resource metrics, query/lock data, queue
and cache data, traces, provider responses, reconciliation output, defect links, owner, and signed
Go/No-Go decision. Any missing business reconciliation converts the run to **inconclusive**, not pass.
