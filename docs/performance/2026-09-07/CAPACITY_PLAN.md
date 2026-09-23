# LiftHaul provisional capacity plan

Date: 2026-09-07  
Status: **engineering assumptions awaiting Product, Finance, Operations, and Growth approval**

This forecast exists so the platform is not designed around demonstration traffic. None of the
figures below are production analytics or contractual commitments. They must be replaced by an
approved business forecast and then recalibrated monthly after launch.

## Population and transaction forecast

| Measure | Launch | 3 months | 1 year | 3 years |
|---|---:|---:|---:|---:|
| Registered bookers | 5,000 | 20,000 | 100,000 | 500,000 |
| Daily active bookers | 500 | 2,500 | 12,000 | 60,000 |
| Registered provider/fleet accounts | 500 | 2,000 | 10,000 | 50,000 |
| Active drivers | 1,000 | 4,000 | 20,000 | 100,000 |
| Registered vehicles/equipment | 800 | 3,000 | 15,000 | 75,000 |
| Normal concurrent users | 100 | 500 | 2,500 | 10,000 |
| Peak booking submissions/minute | 10 | 50 | 250 | 1,000 |
| Simultaneous quotation requests/minute | 20 | 80 | 400 | 1,500 |
| Payment transactions/minute | 5 | 20 | 100 | 400 |
| Payment webhooks/second, burst | 2 | 10 | 50 | 200 |
| Active-trip location updates/second | 25 | 100 | 500 | 2,500 |
| Notification events/minute | 100 | 500 | 2,500 | 10,000 |
| Document/evidence uploads/hour | 100 | 500 | 2,500 | 10,000 |
| Cumulative historical bookings | 10,000 | 50,000 | 1,000,000 | 10,000,000 |

## Surge models

- Marketing campaign: 100 to 5,000 concurrent users inside one minute; quotation demand at five
  times launch peak for 15 minutes.
- Corporate batch: 500 booking submissions in one authorized batch while normal traffic continues.
- Holiday/weather surge: twice forecast peak for four hours, with map and notification degradation.
- Webhook replay burst: 10 times normal webhook rate for five minutes; every event must remain
  idempotent and durably queued.
- Nationwide emergency: location traffic at three times peak while non-critical reports are paused.

## Provisional SLOs and integrity objectives

| Indicator | p50 target | p95 target | p99 target | Error/integrity target |
|---|---:|---:|---:|---|
| Standard browser/API read | ≤ 500 ms | ≤ 2 s | ≤ 4 s | < 1% normal load |
| Critical authenticated API | ≤ 300 ms | ≤ 1 s | ≤ 2.5 s | < 1% normal load |
| Booking submission | ≤ 1 s | ≤ 3 s | ≤ 6 s | zero lost/duplicate bookings |
| Vehicle recommendation | ≤ 1 s | ≤ 3 s | ≤ 6 s | zero unsafe matches |
| Dashboard load | ≤ 1 s | ≤ 3 s | ≤ 6 s | totals reconcile to records |
| Payment-webhook durable acknowledgement | ≤ 500 ms | ≤ 2 s | ≤ 4 s | zero duplicate financial effects |
| Monthly availability | — | — | — | ≥ 99.9% |

At peak load, a temporary error rate up to 2% is acceptable only for explicitly non-critical reads.
Booking, payment, release, refund, dispatch, and dispute integrity remain zero-tolerance objectives.

## Recovery objectives requiring business approval

| Asset/service | Proposed RPO | Proposed RTO | Required proof |
|---|---:|---:|---|
| Transactional booking/payment database | ≤ 5 minutes; zero loss for provider-confirmed payments through replay/reconciliation | ≤ 30 minutes for zone failover; ≤ 4 hours for regional disaster | Managed PostgreSQL PITR plus restore and reconciliation exercise |
| Payment webhook/event queue | 0 acknowledged events | ≤ 15 minutes | Durable replicated queue failover and replay |
| Documents/POD/object storage | ≤ 15 minutes | ≤ 4 hours | Versioning, replication, restore test, hash reconciliation |
| Cache/session/rate-limit store | No authoritative business data | ≤ 15 minutes | Rebuild/failover exercise; application degrades safely |
| Location-event stream | ≤ 5 minutes while offline buffers remain available | ≤ 30 minutes | Broker outage and replay exercise |

## Test-stage mapping

| Stage | Purpose | Duration minimum | Exit condition |
|---|---|---:|---|
| 1 user | Baseline | 10 minutes | Stable result and dependency timings |
| 100 | Launch normal load | 30 minutes | All SLO and integrity gates pass |
| 500 | Three-month normal/launch peak | 30 minutes | All SLO and integrity gates pass |
| 1,000 | Intermediate peak | 30 minutes | Graceful behavior and no integrity defect |
| 2,500 | One-year normal | 60 minutes | All SLO and integrity gates pass |
| 5,000 | Campaign spike | 15-minute spike plus 60-minute recovery | Autoscale and queues recover |
| 10,000 | Three-year normal candidate | 60 minutes | All approved targets pass |
| Beyond forecast | Stress/break point | Until controlled failure | Safe capacity and first bottleneck recorded |
| Approved peak | Soak | 8–24 hours | No leak, buildup, drift, or stuck transaction |

Capacity is approved at the highest stage that passes twice on separate days with production-parity
infrastructure, mature data volume, complete observability, and post-run business reconciliation.
