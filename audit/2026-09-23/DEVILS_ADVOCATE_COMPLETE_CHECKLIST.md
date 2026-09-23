# LiftHaul complete devil's-advocate checklist

**Assessment date:** 23 September 2026

**Scope:** the hostile product audit mandate, competitor benchmark mandate, and performance/scalability mandate supplied by the owner.

**Release rule:** code presence is not a pass. A row is crossed out only when an implemented control has repeatable evidence. Live-provider and production-scale claims require live evidence.

## Status legend

- ~~✅ EXISTING~~ — implemented and covered by repository or production evidence.
- ~~🆕 ADDED~~ — implemented in the current driver-registration change; production deployment is still a separate gate.
- 🟡 PARTIAL — useful implementation exists, but the complete business result is not proven.
- ⛔ MISSING — not implemented or not operational.
- 🔒 EXTERNAL GATE — needs owner/provider/legal/regulatory/production evidence and cannot be truthfully crossed out in code.
- 🧪 NOT PROVEN — design/test tooling exists, but required execution evidence is absent.

## Executive result

| Area | Status | Evidence-based conclusion |
|---|---|---|
| Public site and API | 🟡 PARTIAL | GitHub Pages points to Railway; `/health` and `/readyz` responded on 23 Sep. This does not prove every journey or live providers. |
| Driver onboarding | ~~🆕 ADDED~~ | Dedicated form, durable application, contact OTP, governed sponsorship, canonical driver/principal binding, and no self-verification are implemented and tested. Deployment pending this change. |
| Booking and matching | 🟡 PARTIAL | Cargo/vehicle taxonomy and gates exist; atomic public-booking idempotency and all hostile route/cargo cases remain open. |
| Protected payment | 🟡 PARTIAL | State machine, ledger, dispute/release gates and Xendit adapter exist; live money movement is not approved or proven. |
| Xendit | 🔒 EXTERNAL GATE | Adapter and mock/sandbox-facing controls exist. Production mode requires credentials plus seven explicit approval/readiness flags. Not live-complete. |
| Wise | 🔒 EXTERNAL GATE | Mock and fail-closed real-adapter seam exist. Real adapter still reports credential validation required. Not live-complete. |
| Fleet/driver/provider workspaces | 🟡 PARTIAL | Role-scoped services exist; moderated real-user and large-fleet evidence is missing. |
| Security/privacy/regulatory | 🟡 PARTIAL | Strong RBAC/tenant/audit controls exist; pentest, counsel/DPO sign-off, live verification partners and regulatory claims remain gated. |
| Performance/reliability | 🟡 PARTIAL | SLOs, load tooling, backpressure and integrity tests exist; 100–10,000 user, soak, failover, DR and autoscale evidence is absent. |
| Public release | **NO-GO** | Critical/High production-evidence gates remain. Controlled non-financial pilot only after the listed gates are closed. |

## A. Roles and complete journeys

### A1. Client booker

- 🟡 Understand product in five seconds / immediately find booking CTA — improved homepage, not moderated-tested.
- ~~✅ Book-a-service route and cargo-first form exist.~~
- 🟡 Registration, verification, login, logout and password recovery — services exist; complete public production journey needs retest.
- 🟡 Explain why each requested field/document is required — some inline copy exists; not complete across all forms.
- 🟡 Distinguish draft, quotation, confirmed, funded and active delivery — state models exist; older demo contradictions require regression review.
- ~~✅ Vehicle recommendation considers cargo taxonomy, weight and eligibility.~~
- 🟡 Prevent undersized vehicle and unrealistic/missing weight — logic exists, but the earlier hostile legacy bypass remains an open retest item.
- 🟡 Unknown-weight workflow — not proven as safe assisted estimation.
- ~~✅ Quote model contains transport, 10% administration fee and applicable tax controls.~~
- 🟡 Show tolls, permits, ferry/RoRo, parking and exclusions consistently — copy/rules exist in parts; full journey reconciliation not proven.
- 🟡 Explain who holds funds, protection start and release — policy/state content exists; live provider/legal model is gated.
- ~~✅ Dispute can block release in unit/integration tests.~~
- 🟡 View protected-payment transactions, invoice and records — implemented surfaces; no live statutory chain.
- 🟡 Live tracking and privacy-preserving driver contact — trip/GPS domain exists; live location/communications not proven.
- 🟡 Late driver, no-acceptance, cancellation, refund and final-price-change exceptions — state paths exist unevenly; end-to-end UAT missing.
- ~~✅ Delivery evidence and recipient-verification controls exist.~~

### A2. Independent truck owner/provider

- ~~✅ Separate provider registration and canonical carrier application exist.~~
- 🟡 Document upload/rejection/correction — metadata/workflow exists; secure object storage and malware scanning missing.
- ~~✅ Vehicle suitability, registration, compliance and duplicate-domain controls exist in canonical onboarding.~~
- ~~✅ Double assignment and assignment/payment gates have deterministic tests in serialized pilot mode.~~
- 🟡 See funded state before work — implemented projection; live provider evidence absent.
- 🟡 Full job details and excluded expenses before acceptance — partial UI/API evidence.
- 🟡 Client changes, overweight cargo, unsafe/illegal refusal and waiting-time evidence — exception/change domains exist; moderated E2E absent.
- 🟡 Breakdown, reassignment and assistance — reassignment implementation exists; production operational support absent.
- ~~✅ Dispute freezes release and evidence is audit-linked in tests.~~
- 🟡 Fee/tax/net earnings and payout reconciliation — ledger exists; live payout chain absent.
- 🟡 Failed payout and manual deduction controls — fail-closed/maker-checker logic exists; real provider exercise absent.
- 🟡 Rating retaliation protection — trust domain exists; adversarial production exercise absent.

### A3. Fleet owner

- ~~✅ Multiple vehicle/driver registration, status, assignment and role-scoped carrier portal exist.~~
- 🟡 Bulk upload for hundreds of vehicles/drivers — backend batch capabilities are incomplete as a polished fleet workflow.
- ~~✅ Expiry and compatibility gates exist for drivers/vehicles.~~
- 🟡 Available/booked/maintenance/suspended operational view — domain support exists; 100-vehicle usability not tested.
- 🟡 Dispatcher vs finance vs owner staff separation — platform RBAC exists; fleet-subuser UX and real organizational UAT are incomplete.
- 🟡 Concurrent dispatchers and same-vehicle races — serialized pilot prevents concurrency; pooled/multi-instance proof is open.
- 🟡 One-screen trip monitoring, reports and exports — partial; mature-data and fleet-user evidence absent.
- 🟡 Gross/fee/tax/net reconciliation — internal ledgers exist; end-to-end live payout/accounting absent.
- 🟡 Driver performance, dispute and former-staff offboarding — controls exist in parts; moderated lifecycle not proven.
- 🧪 Hundreds of drivers/vehicles and thousands of history records — production-sized dataset test not run.

### A4. Driver

- ~~🆕 Dedicated public driver form no longer routes into company/fleet registration.~~
- ~~🆕 Driver application is durably stored with licence, experience, safety and contact data.~~
- ~~🆕 Contact verification does not falsely activate the driver.~~
- ~~🆕 Explicit fleet/operations sponsorship creates the canonical driver and principal binding.~~
- ~~🆕 Repeated sponsorship is idempotent and does not duplicate the driver.~~
- ~~✅ Driver cannot self-verify or self-activate compliance.~~
- ~~✅ Own-trip scoping, dispatch acceptance, status, GPS, POD, recipient OTP verification and exception reporting exist.~~
- 🟡 Navigation, communication, breakdown/accident/delay support — workflows are partial and live services absent.
- 🧪 Offline delivery, GPS denial/inaccuracy, reconnect and low-battery behaviour — not proven.

### A5. Other service provider

- ~~✅ Heavy equipment/provider types, equipment taxonomy and managed industrial project/resource plans exist.~~
- ~~✅ Survey and resource-plan approval gates exist.~~
- 🟡 Operator/equipment matching, schedules, transparent rates, completion evidence and settlement — implemented in parts; real provider pilot absent.
- 🔒 Licensing/certification verification — requires current official/qualified verification evidence.

### A6. Operations, finance, compliance and support

- ~~✅ Server-enforced RBAC, tenant scoping, audit records and maker/checker patterns exist.~~
- ~~✅ User lifecycle, suspension and session revocation controls exist.~~
- 🟡 User/vehicle/document verification and booking/payment monitoring — implemented; production staffing and runbooks not proven.
- 🟡 Dispute, fraud, exception and settlement operations — implemented controls; live exercises absent.
- ⛔ Public support case intake, named escalation path and operational SLA are not fully live/proven.
- 🧪 Least-privilege effectiveness needs independent access review and penetration testing.

## B. Competitor benchmark checklist

Classification is against product capability, not visual imitation.

| Capability | LiftHaul standing | Required closure |
|---|---|---|
| Registration effort / time to first booking | 🟡 Inferior/Unproven | Moderated first-time-user study and funnel analytics |
| Vehicle selection/capacity/weight/dimensions | 🟡 Potential differentiator | Close every bypass; explain recommendations |
| Price visibility | 🟡 Potential differentiator | Prove total reconciliation and exclusions |
| Immediate/scheduled booking | 🟡 Partial | Production end-to-end evidence |
| Multiple stops / inter-island / RoRo | 🟡 Partial | Routing, pricing and availability proof |
| Corporate account and team access | 🟡 Partial | Fleet/client team UX and UAT |
| Fleet management and driver/vehicle pairing | 🟡 Potential differentiator | Large-fleet and concurrency evidence |
| Real-time tracking | ⛔ Inferior | Live GPS architecture/provider and privacy controls |
| In-app communication/notifications | ⛔ Inferior | Connect and operate email/SMS/push provider |
| POD and delivery verification | 🟡 Potential differentiator | Live mobile/offline pilot evidence |
| Receipts/expense reports | 🟡 Partial | Statutory/accounting sign-off |
| Customer support | ⛔ Missing | Live support channel, SLA and escalation |
| Cancellation/refunds/disputes | 🟡 Potential differentiator | Live payment and operational drills |
| Ratings | 🟡 Partial | Abuse/retaliation controls and product UX |
| Cargo protection/insurance | 🟡 Regulatory concern | Partner, policy, claims and legal approval |
| Promotions/referrals | ~~✅ Governed single-level referral engine exists and is feature-flagged.~~ | Production campaign governance before enablement |
| APIs/business integrations | 🟡 Partial | Partner sandbox, docs and production clients |
| Accessibility | 🧪 Unproven | WCAG audit with assistive technology |
| Mobile performance/low connectivity | 🧪 Unproven | Device/network matrix and offline design |

## C. Functional controls

- 🟡 Registration, identity, login/logout, recovery and role access — implementation exists; every role needs production E2E evidence.
- 🟡 Booker, fleet, provider, driver and staff dashboards — surfaces exist; cross-role moderated UAT absent.
- ~~✅ Vehicle, driver, document, cargo, weight, vehicle-matching, quotation, booking, dispatch and trip domains exist.~~
- 🟡 Map/route selection and serviceability — partial; live map dependency and edge locations unproven.
- 🟡 Protected payment, verification, settlement, cancellation, refund and dispute — strong internal tests; no live-provider financial evidence.
- 🟡 Messaging, ratings, notifications, reporting and audit — partial operational readiness.
- 🧪 Every visible control/state, filters/search/sort/pagination/upload/download/date/dropdown/responsive layout — no complete evidence run.
- 🧪 Every dashboard number reconciles to exactly the same filtered records — no complete dashboard reconciliation suite.

## D. Booking stress matrix

- 🟡 Very small, maximum, exactly-at-limit and 1 kg-over-limit — matcher logic exists; complete public-journey regression required.
- 🟡 Missing/zero/negative/decimal/nonnumeric/huge weight and kg-vs-ton mistakes — validation exists in parts; hostile bypass retest remains required.
- 🟡 Heavy-compact and light-oversized — dimensions/taxonomy supported; safety proof incomplete.
- 🟡 Fragile/refrigerated/hazardous/high-value cargo — categories/risk rules exist; provider/regulatory operations not proven.
- 🟡 Multiple pickup/destination and inter-island/RoRo — partial design/implementation; live routing/pricing absent.
- 🧪 Remote barangay, renamed/duplicate location, unsupported road and boundary cases — not comprehensively tested.
- 🟡 Same-address, past schedule, last-minute, holiday and overnight — some validation; complete state/evidence missing.
- 🟡 No vehicle, multiple eligible vehicles and multiple-truck requirement — matcher/resource planning exists; live capacity proof absent.
- ~~✅ Recommendation is not intentionally weight-only; cargo class and eligibility gates are present.~~

## E. Concurrency and race conditions

- 🟡 Two providers accepting one booking — serialized pilot safe; pooled/multi-instance atomic proof open.
- 🟡 Two dispatchers assigning one truck — serialized pilot safe; database-enforced multi-instance proof open.
- ~~✅ Repeated Pay/idempotent payment command and duplicate webhook controls have tests.~~
- 🟡 Confirmation after cancellation and client/provider simultaneous edit — state controls exist; adversarial production test absent.
- 🟡 Two dispute resolvers, duplicate POD and dispute-during-release — maker/checker/state tests exist in parts; pooled race proof incomplete.
- 🟡 Document expiry during trip and completion-versus-hold — policy/state behavior exists; end-to-end chaos test absent.
- ⛔ Public booking idempotency still uses read-before-write and lacks the required production PostgreSQL uniqueness/race proof.

## F. Payment and protected-payment matrix

- 🟡 Success/failure/cancel/abandon/expired link/delayed confirmation — adapter/state support exists; live Xendit execution absent.
- ~~✅ Duplicate/forged/bad-signature webhook and amount/reference mismatch controls exist in tests.~~
- 🟡 Wrong currency, duplicate transaction ID, timeout and post-expiration completion — test coverage exists in parts; provider sandbox evidence incomplete.
- ~~✅ Full/partial refund caps, release gates, dispute/hold and reconciliation logic exist.~~
- 🟡 Failed refund/release, invalid payout and unauthorized staff release — fail-closed logic; live operational evidence absent.
- ~~✅ Funded/protected state requires authenticated provider evidence in the gateway design.~~
- 🔒 Xendit production readiness — not complete; credentials, channel certification, pilot, reconciliation, regulatory/safeguarding, independent security and DR flags are required.
- 🔒 Wise production readiness — not complete; real profile/credential validation and sandbox-to-live transfer/reconciliation evidence required.

## G. Pricing and tax

- ~~✅ Canonical model is transport charge + 10% administration fee + applicable tax.~~
- 🟡 Excluded toll/parking/ferry/RoRo/port/permit costs are modeled/disclosed but need journey-wide consistency.
- ~~✅ 10%, decimal/rounding, discounts, revised bookings, effective-dated tax and snapshot controls have tests.~~
- 🟡 VAT/non-VAT, inclusive/exclusive display, withholding, cancellations and partial completion — implemented in parts; Philippine tax sign-off absent.
- ~~✅ Third-party excluded costs are not intended to enter the protected total/admin-fee base.~~
- 🧪 Quote → checkout → ledger → invoice → payout → accounting export peso-for-peso reconciliation has not been proven with live transactions.

## H. Security and abuse

- ~~✅ Tenant/object access, server RBAC, role escalation prevention and audit controls have automated tests.~~
- ~~✅ Protected actions are permission-gated and sensitive state changes are audited.~~
- 🟡 Duplicate vehicle/identity and impersonation controls — canonical constraints/workflows exist; adversarial identity validation absent.
- ~~✅ Price/payment mutation and duplicate refund/release controls exist at service layer.~~
- 🟡 Password reset, token reuse, guessing/lockout and scraping/rate limits — controls exist; independent penetration evidence missing.
- 🟡 XSS/SQL injection and malicious payload handling — parameterization/input caps exist; pentest missing.
- ⛔ Secure binary object storage, type/signature validation, malware scanning, quarantine and signed downloads are not fully operational.
- 🔒 All aggressive security testing must remain in an authorized isolated environment.

## I. Connectivity, devices, maps and usability

- 🧪 Slow/intermittent/offline/reconnect and duplicate-submit recovery — not proven across journeys.
- 🧪 Older Android, current iPhone, tablet, desktop, small screen and landscape matrix — responsive CSS exists; device evidence absent.
- 🧪 Refresh/back/app-close during payment and booking — not proven.
- 🧪 Driver offline, GPS unavailable/inaccurate and low-battery mode — not proven.
- 🟡 Location hierarchy/address/coordinates/markers/routes/serviceability — domain and map UI exist; complete geographic test matrix absent.
- 🧪 Observe unassisted users for hesitation, protected-payment comprehension, exclusions, vehicle choice, price trust and next action — moderated research absent.

## J. Failure, chaos and operations

- 🧪 Map/payment/SMS/email/database/queue/storage/certificate/API throttling/webhook/deployment/restart/outage scenarios — fail-closed designs exist in parts; hosted chaos evidence absent.
- 🟡 Honest error messages and transaction preservation — several services fail closed; complete recovery proof absent.
- 🟡 Consent, notices, retention, verification records, audit trails, complaints/refunds/disputes/approvals/suspension/legal hold/incidents/tax/settlements — data models exist unevenly; operating approval absent.
- ~~✅ Unsupported claims are prohibited in repository guidance.~~
- 🔒 Never claim BSP approval, LTFRB approval, licensed escrow or full insurance without exact current documentary evidence.

## K. Judgmental commercial questions

- 🟡 Trust for high-value cargo — governed evidence and payment design are credible, but live-provider/legal/operations proof is missing.
- 🟡 Fleet incentive — portal and protected-payment controls are useful, but real demand/liquidity and payout reliability are unproven.
- 🟡 Material differentiation — governed heavy-haul workflow is plausible; customer validation absent.
- 🧪 10% fee value — no customer willingness-to-pay evidence.
- 🟡 Protected payment protects both parties in design; it is not a proven live service.
- 🟡 Final-price and net-earnings clarity — models exist; unassisted-user evidence missing.
- 🟡 Evidence-based disputes — audit/evidence domains exist; staffed operational trial absent.
- 🧪 Fraud, poor connectivity and exception survival — no complete pilot/chaos proof.
- 🟡 Staff controls are strong in code; independent control effectiveness review absent.
- 🧪 100× traffic — not proven.
- **No:** a founder should not yet entrust a ₱1 million live transaction to this system.
- **No evidence:** a real fleet has not placed 100 vehicles under production control.
- 🧪 First-time booking without guidance — not moderated-tested.
- 🟡 Defensibility is governance/domain execution, not the visible UI; market validation is still required.

## L. Evidence, severity and release gates

- ~~✅ Critical/High/Medium/Low definitions are adopted in the defect registers.~~
- 🟡 Test-ID, role, scenario, preconditions, data, steps, expected/actual, evidence, severity, owner, due date, retest and status exist for the 3 Sep audit; new work must keep the same standard.
- ⛔ Critical and High findings are not all resolved/retested; production public release remains blocked.
- 🟡 Thirteen mandated deliverables exist in the 3 Sep audit package, but several are now stale and require production-era retesting.
- 🟡 This checklist supersedes “feature complete” claims; it does not supersede raw evidence or signed approvals.

## M. Performance, scalability and infrastructure checklist

### M1. SLOs and population

- ~~✅ Target SLOs are documented: 99.9%, p95 page/API/booking targets, <1% errors, zero duplicates/loss.~~
- 🧪 p50/p95/p99 production measurements and monthly availability history are not yet recorded.
- ⛔ RPO and RTO have not been formally demonstrated on production.
- ⛔ Launch/3-month/1-year/3-year/campaign/nationwide/seasonal population forecast needs approved business inputs.

### M2. Test levels and mixed workload

- ~~✅ Baseline→normal→peak→stress→recovery stages and mixed-role workload are documented.~~
- 🟡 Load tooling and graceful backpressure exist.
- ⛔ 100, 500, 1,000, 2,500, 5,000 and 10,000 simultaneous-user stages have not been executed on production-equivalent infrastructure.
- ⛔ Breaking point, sustainable TPS, first bottleneck and recovery after overload are unknown.
- 🧪 Mixed journeys (browse, auth, cargo, recommendation, booking, payment, webhook, tracking, upload, fleet, staff) need executed evidence.

### M3. Critical journey correctness under load

- 🧪 Full booker journey through invoice under concurrency — not proven live.
- 🧪 Full fleet journey through payout/reporting under concurrency — not proven live.
- 🧪 Full driver journey through POD/completion under concurrency — not proven live.
- ~~✅ Unique references, idempotency records, transactions, webhook verification and immutable audit patterns exist for financial flows.~~
- ⛔ Public booking and pooled assignment winner constraints require atomic PostgreSQL closure before horizontal scale.

### M4. Architecture and single points of failure

- 🟡 Railway + PostgreSQL production is now hosted and health/readiness are reachable.
- ⛔ CDN/WAF/DDoS/bot controls and health-aware load balancing are not evidenced as a complete architecture.
- ⛔ At least two stateless application instances with tested replacement/autoscale are not evidenced.
- 🟡 Managed PostgreSQL is used; HA/multi-zone/failover/PITR/restore evidence is absent.
- ⛔ Managed cache, durable queue and production object storage/malware pipeline are not complete.
- 🧪 Loss of app instance/AZ/DB/cache/queue/storage/map/payment/SMS/email/monitoring has not been systematically tested.

### M5. Environments, IaC and deployment

- 🟡 Local/test/CI/production exist in some form; QA/performance/UAT/staging/DR parity is not demonstrated.
- 🟡 Docker/render/Railway configuration is versioned; complete infrastructure-as-code for network/security/cache/queue/storage/monitoring is absent.
- 🟡 CI and health gates exist; canary/blue-green, automatic rollback and backward-compatible migration exercise are not fully evidenced.
- 🧪 Failed-deployment rollback without financial inconsistency needs a production-like exercise.

### M6. Load, spike, endurance and large data

- ⛔ Expected/peak load report with percentiles, throughput, resources and reconciliation is absent.
- ⛔ Stress-to-breaking-point and graceful rejection evidence is absent.
- ⛔ 100→5,000-in-one-minute spike and webhook/location/notification storm evidence is absent.
- ⛔ 8–24 hour soak test for leaks, queues and stuck transactions is absent.
- ⛔ Millions-of-bookings/GPS/ledger/documents/history mature-data test is absent.

### M7. External APIs and location scale

- 🟡 Payment idempotency and provider timeouts/fail-closed posture exist.
- 🟡 Explicit retries/backoff/circuit breakers are not complete for every map/SMS/email/identity/document/analytics/government provider.
- 🟡 GPS uses a dedicated trip-location table rather than booking columns, but high-volume stream/time-series architecture and adaptive frequency are not complete.
- ⛔ Offline buffering, retention/privacy policy and dashboard refresh limits need production proof.

### M8. Observability, alerts, DR, capacity and cost

- 🟡 Structured logs, health/readiness and business audit exist.
- ⛔ Central metrics/tracing/error/queue/payment/provider/security dashboards are not fully evidenced.
- ⛔ Actionable critical/high alert routing, on-call ownership and runbooks are not proven.
- 🟡 Backup scripts exist; scheduled restore, deletion/corruption/migration/credential/region/ransomware exercises are absent.
- ⛔ Safe capacity, headroom, growth runway, scaling trigger and cost estimate are unknown.
- ⛔ Budgets, cost alerts, per-service usage, storage lifecycle and log-retention controls need evidence.

### M9. Performance release decision

- ⛔ Do not pass a release while SLOs, failover, queue recovery, restore, monitoring or overload recovery lack evidence.
- ~~✅ Duplicate financial transactions and lost confirmed bookings are defined as zero-tolerance gates.~~
- **Current decision: NO-GO for unrestricted public financial production; controlled pilot eligibility requires closure of Critical/High defects and live-provider, security, DR and UAT gates.**

## N. Current change evidence

| Evidence ID | Result |
|---|---|
| DRV-NEW-001 | `backend/test_public_driver.py` + provider/driver regression: 38 passed locally on 23 Sep 2026. |
| DRV-NEW-002 | Driver application remains `PENDING_DRIVER_REVIEW` after OTP; login is blocked. |
| DRV-NEW-003 | Sponsorship creates canonical `mkt_drivers` + `driver_principals`; compliance status remains `APPLICATION`. |
| DRV-NEW-004 | Production posture never returns the OTP when no messaging provider is connected. |
| UI-NEW-001 | Homepage driver CTA/footer now point to `driver-register.html`. |
| UI-NEW-002 | Homepage hero CTAs are compact, content-width actions on desktop. |
| PROD-001 | Railway `/health` = `ok`, `/readyz` = `ready`, schema 24 before this change was deployed. |
| PAY-001 | Xendit `PAYMENT_GATEWAY_MODE=production` startup gate requires credentials and explicit provider/pilot/reconciliation/regulatory/safeguarding/security/DR approvals. |
| PAY-002 | Wise real adapter reports `LIVE WISE BLOCKED` until owner credential/profile validation is completed. |

## O. Immediate prioritized backlog

1. Deploy and production-smoke the driver application migration (schema 25) and form.
2. Configure a real email/SMS OTP provider; production currently fails closed if delivery is unavailable.
3. Close atomic public-booking idempotency and stable replay response on PostgreSQL.
4. Re-run the 3 Sep Critical/High defect register against the current deployed system and publish retest evidence.
5. Complete Xendit sandbox certification and all production activation gates; do not enable live collection early.
6. Validate Wise business profile/credentials and sandbox transfer/reconciliation; do not claim completion beforehand.
7. Implement secure binary object storage, scanning/quarantine and signed access.
8. Run independent security testing and moderated multi-role UAT.
9. Execute production-equivalent load, spike, soak, failover and restore tests; publish p50/p95/p99 and correctness results.
10. Obtain legal, privacy, tax, transport and payment operating approvals before unrestricted public financial launch.
