# Functional, security, performance, mobile, connectivity, and chaos results

## Functional results

Status meanings: **Pass** = observed business result matched the stated expectation in the tested scope; **Fail** = observed contradiction; **Blocked** = required live dependency absent; **Partial** = only a narrower result was proved.

| Test ID | Role/scenario | Preconditions and data | Steps | Expected | Actual | Status / severity | Evidence |
|---|---|---|---|---|---|---|---|
| FUN-001 | Visitor understands service | Deployed landing | Open page; inspect heading and primary action | Purpose and next action clear | Proposition and Book a Service are visible | Pass | EV-WEB-001 |
| FUN-002 | Client valid login | Hosted API and valid user required | Open client login | Authenticate and reach correct profile | Button disabled; API absent | Blocked / High | EV-WEB-003 |
| FUN-003 | Client invalid login/recovery/lockout | Hosted auth required | Attempt invalid login/reset | Correct error, rate limit, reset, session controls | Cannot execute in deployed site | Blocked / High | EV-WEB-003 |
| FUN-004 | Cargo-first sequence | Deployed booking | Inspect DOM order | Locations/cargo/handling before vehicles | Correct visible sequence | Pass (UI only) | EV-WEB-002 |
| FUN-005 | Missing weight | Local temporary DB; motorcycle | Submit without `weight_kg` | Reject and explain correction | Accepted using `LEGACY_CAPACITY_ONLY` | Fail / Critical | EV-PROBE-001 |
| FUN-006 | Nonnumeric weight | Local temporary DB; `not-a-number` | Submit booking | Reject invalid number | Parsed to null and accepted as legacy | Fail / Critical | EV-PROBE-001 |
| FUN-007 | Incomplete location | Only island groups; valid 10 kg cargo | Submit booking | Reject missing region/province/city/barangay/address | Accepted | Fail / High | EV-PROBE-002 |
| FUN-008 | Safety allowance boundary | 1,000 kg van, 10% allowance | Recommend at 909 kg and 910 kg | Van eligible below usable capacity; not above | Van offered at 909, removed at 910 | Pass | EV-PROBE-003 |
| FUN-009 | Refrigerated/dimension gates | Automated fixtures | Run full suite | Only suitable options | Targeted tests pass | Pass (synthetic) | EV-AUTO-001 |
| FUN-010 | Splittable multi-vehicle | 12,000 kg refrigerated, 4 units | Run matching test | Allocate 2+ eligible vehicles | Targeted test passes | Pass (synthetic) | EV-AUTO-001 |
| FUN-011 | Hazardous/manual assessment | Hazardous 200 kg fixture | Run matching test | No unsafe standard guess | Manual assessment returned | Pass (synthetic) | EV-AUTO-001 |
| FUN-012 | Dashboard total | Synthetic client workspace | Click Total bookings | Show exactly total records | 7 records across 2 pages | Pass (demo) | EV-WEB-004 |
| FUN-013 | Active dashboard filter | Synthetic client workspace | Click Active bookings | Show exactly 3 active | 3 records shown | Pass (demo) | EV-WEB-004 |
| FUN-014 | Payment status/action | Synthetic bookings 1046/1041 | Inspect list/detail | Mutually consistent states and allowed actions | Payment Required + Protected + Pay Now; dispute + Confirm Delivery | Fail / High | EV-WEB-005/006 |
| FUN-015 | Notification centre | Synthetic client workspace | Open/filter/mark records | Real durable user notification state | Demo UI only; no provider or live account | Partial / High | EV-WEB-003 |
| FUN-016 | Provider onboarding | Deployed provider page | Submit/verify | Create private workspace and correction flow | No hosted API | Blocked / High | EV-WEB-008 |
| FUN-017 | Driver registration/login | Homepage to driver path | Follow CTA; inspect driver login | Direct registration/auth/recovery | CTA dead-end; login lacks recovery; API absent | Fail / High | EV-WEB-008/010 |
| FUN-018 | Staff login/recovery | Deployed staff page | Open forgot password | Dedicated secure recovery | `href="#"`; developer connection instructions exposed | Fail / High | EV-WEB-009 |
| FUN-019 | Support escalation | Deployed support | Find contact/ticket/SLA | Reachable support and escalation | None available | Fail / High | EV-WEB-011 |
| FUN-020 | Responsive semantics | 390 × 844 viewport | Open landing/booking snapshots | Controls remain reachable and labelled | Semantic controls remained; visual/touch/AT certification incomplete | Partial | EV-WEB-013 |

## Concurrency and race-condition results

| Test ID | Scenario | Expected | Actual | Judgment |
|---|---|---|---|---|
| CON-001 | 24 simultaneous booking submissions, same idempotency key | One persisted booking and contract-equivalent response to every caller | One run persisted 2 bookings; repeat persisted 1 | **Fail / High**; read-before-write is not an atomic guarantee. |
| CON-002 | Replay response contract | Same schema as original submit | Replay returns tracking projection; probe callers received missing `booking_id` errors | **Fail / High**; clients cannot safely retry. |
| CON-003 | Duplicate payment webhook | One financial transition | Automated tests reject/deduplicate replay | Pass in mock/unit scope only. |
| CON-004 | Payment amount mismatch | Never paid/protected | Automated test puts transaction under review | Pass in mock/unit scope only. |
| CON-005 | Release during open dispute | Release denied | Red-team unit test denies | Pass in mock/unit scope only. |
| CON-006 | Two providers/dispatchers accept or assign simultaneously | Exactly one winner | No production-equivalent concurrent HTTP/database evidence | Blocked / High readiness gap. |
| CON-007 | Delivery proof submitted twice | One durable proof/event | No production file/GPS event exercise | Blocked. |
| CON-008 | Hold/dispute racing with release | Hold wins or transaction is safely serialized | State-machine unit tests exist; no true provider/database race exercise | Partial. |

## Security findings

### Positive implementation evidence

- Backend tests cover role checks, tenant isolation, guarded login/lockout, session handling, webhook authentication, replay rejection, amount mismatch, refund state, payout gates, and audit records.
- Payment tests require provider webhook plus provider API confirmation before paid status.
- Live-funds and external-provider feature flags default off, which is the correct fail-closed posture.
- Client-safe tracking tokens are used instead of sequential booking IDs in the public tracking design.

### Release-blocking gaps

- No independent penetration test or production configuration review exists. IDOR, cross-tenant access, staff escalation, reset-token abuse, session fixation, secret management, CSP, CORS, WAF, rate limits, and audit immutability are therefore **not certified**.
- Public auth is not deployed, so its real password, MFA, lockout, reset, expiry, logout, and terminated-staff behavior cannot be observed.
- The generic file route records metadata/checksums; the server does not pass file bytes into the validator. Durable object storage, magic-byte validation, antivirus/sandbox scanning, image/PDF sanitization, download authorization, quarantine, and retention are not evidenced.
- Public staff UI exposes local developer connection instructions. Even if not a secret, it increases attack-surface disclosure and user confusion.
- No fraud operations, security monitoring, alerting, on-call escalation, incident exercise, or recovery evidence exists.

Security release status: **NO-GO**, even though the unit controls are directionally strong.

## Performance and stress results

| Measure | Result | Valid conclusion | Not proved |
|---|---:|---|---|
| Full backend regression | 1,361 pass in 714.22 s | Suite is broad and completes on the audit host | Throughput, concurrency, production latency, provider latency |
| Vehicle recommendations | 500 iterations; median 1.24–1.303 ms; p95 1.786–2.189 ms; max 4.424 ms | Pure service-function matching is fast in a warm local process | HTTP, auth, production DB, maps, fleets, contention, cache, network |
| Concurrent booking retry | 24 callers | Exposed duplicate persistence once and unstable response contract | Safe scale |

No credible evidence exists for thousands of simultaneous logins, quote bursts, map calls, high-volume fleet searches, payment-webhook bursts, location streams, notification spikes, document uploads, settlement jobs, or report generation. CPU, memory, queue delay, database saturation, p95/p99 response times, error budgets, recovery time, and data correctness after recovery remain unmeasured.

## Mobile and connectivity

- Landing and booking DOMs were inspected at 390 × 844 and retained labelled controls.
- No older Android, current iPhone matrix, tablet, landscape, browser-version, touch-target, keyboard, screen-reader, zoom, or reduced-motion evidence was produced.
- No offline draft persistence, outbox, idempotent reconnect, payment refresh/back-button handling, GPS-denied behavior, or low-battery/location-throttling behavior exists as tested proof.
- The deployed app's dependence on a missing API makes realistic connectivity recovery impossible to test.

Status: **Partial UI responsiveness; operational mobile/offline behavior missing.**

## Failure and chaos

The code often fails closed when providers are disabled or mocked as failing. That is not a production chaos test. There was no controlled loss of maps, payments, SMS, email, file storage, database primary, queue, certificates, third-party quotas, process restart during payment, or rolling deployment during active trips. Retry safety, dead-letter handling, reconciliation after backlog, recovery point/time objectives, and customer-facing degraded-mode language are not established.

Status: **Not production-tested; High release gap.**

