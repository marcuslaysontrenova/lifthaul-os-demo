# LiftHaul Production Release Audit — 2026-10-09

**Release decision: NO-GO for deployment of candidate `771f02d7d666ecbc1e659329627de02c374ebb70`.**

The candidate is locally committed and its functional, navigation, pricing and cross-browser gates
pass. It has not been pushed or deployed. The existing production service is online, but it serves
different HTML from this candidate and does not expose an immutable release SHA or deployment ID
through its health probes. Hosted recovery, rollback, load, independent security and live-payment
certification evidence also remains open.

## Current production evidence

| Item | Verified state on 9 October 2026 |
|---|---|
| Public URL | `https://www.lifthaul.com.ph/` returned HTTP 200 with HTTPS/HSTS |
| Apex URL | `https://lifthaul.com.ph/` did not resolve |
| Public DNS | `www.lifthaul.com.ph CNAME 9xj5sulr.up.railway.app` |
| Railway project | `trustworthy-intuition`, production environment |
| Railway service | `lifthaul-api`, online |
| Current deployment | `fe901676-c522-427a-977f-b5ba43ab8f47` |
| API | `https://lifthaul-api-production.up.railway.app` |
| API health | `/healthz`: `status=ok`, `env=production` |
| API readiness | `/readyz`: `status=ready`, schema version 27 |
| Live release identity | Not reported by the deployed health endpoints |
| Candidate commit | `771f02d7d666ecbc1e659329627de02c374ebb70` |
| Candidate deployment ID | Not applicable — not deployed |
| Live/candidate equality | **FAIL** — live and candidate `index.html` SHA-256 values differ |

Live index SHA-256 was `BA19121DD507ED9C1519A5550C4E36239D5DA4DFD4BBAF74C0C820B6B93BCE00`.
The tested local candidate index SHA-256 was
`FD8A4FBC6FE4B3DFBD28CB3C959D30734ECF79FF761712F5C5C3132D6690BF60`.

## Completed in the candidate

- Added one shared top navigation component for the public journey, including Home, process,
  services, vehicles, Fare Calculator, Protected Payment, tracking, partner and sign-in routes.
- Preserved contextual Home/Back exits for registration handoffs.
- Added a public fare calculator that uses the same server-side cargo validation, vehicle
  recommendation, tax policy and quote engine as booking; it does not create a booking or payment.
- Removed the active browser-side fare matrix. If the governed pricing service is unavailable, the
  UI fails closed instead of fabricating a total.
- Persisted rate version, effective date and itemized fare components on each booking so historical
  bookings do not silently inherit future rates.
- Added deterministic additional-stop, chargeable-waiting and helper components to the governed
  planning matrix, while leaving tolls, parking, ferry/RoRo, ports, permits and other verified
  third-party expenses excluded.
- Packaged the exact public frontend and API into the same Docker release image. The static server
  allowlist is restricted to public HTML, CSS, JavaScript, the canonical vehicle catalogue and
  assets; arbitrary root JSON is not exposed.
- Retained the canonical 34-vehicle catalogue, unique assets, single selection, persisted vehicle
  code, responsive card layouts and capacity-validation gates.
- Updated the competitor evidence record using official public Lalamove and Transportify sources.
  The matrix remains a planning/test matrix until a commercial approver signs the tariff.
- Kept security-deposit policy at zero and inactive. Benchmark evidence does not support a claim
  that every competitor requires an initial deposit.

## Test evidence

| Gate | Result |
|---|---|
| Complete Python discovery regression | **PASS — 1,456/1,456** |
| Synthetic HTTP lifecycle | **PASS — 31/31 steps** |
| Public UI release gate | **PASS — 24/24** across Chromium, Firefox and WebKit |
| Responsive UAT | **PASS** at desktop, tablet and mobile breakpoints |
| Final affected HTTP/deploy/navigation/theme tests | **PASS — 31/31** |
| Final production-image contract tests | **PASS — 3/3** |
| JavaScript syntax checks | **PASS** for `cargo-booking.js` and `public-nav.js` |
| Source whitespace check | **PASS** after benchmark-document cleanup |
| Hosted PostgreSQL restore/PITR drill | **BLOCKED** |
| Production-like hosted load test | **BLOCKED** |
| Independent penetration test | **BLOCKED** |
| Exact-commit production smoke | **BLOCKED** because candidate is not deployed |

The complete suite continues to emit pre-existing resource-cleanup warnings in some test fixtures.
They did not fail the run, but should be closed as test-harness debt.

## Open defects and release blockers

| Severity | Blocker | Closure evidence |
|---|---|---|
| **Critical** | Live `www` serves a different build from the tested candidate. | Push and merge the candidate, deploy the exact SHA, then prove live asset hashes and health identity match. |
| **Critical** | Production PostgreSQL backup/PITR and isolated restore have not been independently exercised and reconciled. | Successful non-production restore with counts/fingerprint, measured RTO/RPO and retained evidence. |
| **High** | GitHub authentication for `marcuslaysontrenova` is invalid locally, so the candidate and workflows cannot be pushed. | Re-authenticate GitHub and confirm push/Actions access. |
| **High** | The current health probes do not identify the deployed SHA/deployment. | Deploy this revision and verify exact `release_sha` and `deployment_id` through both health probes. |
| **High** | Hosted rollback rehearsal has not been completed. | Roll back a controlled release, verify health/data compatibility, then redeploy the approved SHA. |
| **High** | Production-like load, connection-pool and slow-network recovery evidence is incomplete. | Approved load profile, limits, error rates, alert evidence and capacity decision. |
| **High** | No independent penetration-test report and clean retest are on record. | External report plus closure of all Critical/High findings. |
| **High** | Live-payment legal, provider, safeguarding, webhook, reconciliation, refund, payout and disaster-recovery certifications are incomplete. | Signed payment certification register and approved pilot. |
| **High** | The apex domain does not resolve. | Configure an HTTPS apex redirect or add the apex as a Railway custom domain using Railway's generated DNS target. |
| **High** | The benchmark-informed tariff has not been commercially approved. | Named approver signs rate version, minimums, provider share, tax treatment, cancellation and effective date. |
| **Medium** | The full Playwright suite requires generated `ci/seed_ids.json`; without the PostgreSQL validation setup it is not self-contained. | Make its seed/setup job explicit or generate isolated synthetic fixtures in the Playwright setup. |
| **Low** | Python test fixtures emit unclosed socket/database/file warnings. | Close resources and rerun with those warnings promoted to errors. |

## Payment and payout state

The candidate contains protected-payment records, a payment state machine, maker/checker controls,
provider-beneficiary verification, payout/reconciliation controls and startup fail-closed guards.
Those capabilities make the integration path ready for certification; they do **not** authorize the
platform to hold or move live funds.

The following must remain disabled: live payment collection, card/QR Ph/GCash/Maya/bank collection,
escrow or custody, automated provider payout, automated refund and Wise administration-fee transfer.
Activation requires a contracted licensed provider, confirmed legal role and customer terms,
production credentials stored outside source, verified webhooks and idempotency, beneficiary/KYB
controls, daily reconciliation, refund/dispute operations, an isolated restore drill, an independent
security test and an approved limited pilot. LiftHaul must not be described as BSP-approved,
LTFRB-approved or a licensed escrow provider without verified documentation.

## Domain completion

The `www` record is already present and HTTPS works:

| Name | Type | Value | State |
|---|---|---|---|
| `www` | `CNAME` | `9xj5sulr.up.railway.app` | Present |

For the apex, do not guess or copy a Railway target before registering the apex custom domain in the
Railway service. Use one of these controlled options:

1. Preferred: add `lifthaul.com.ph` as a Railway custom domain, then create the exact CNAME/ALIAS or
   flattened record Railway displays. Wait for DNS validation and certificate issuance.
2. If the registrar cannot flatten an apex CNAME, configure an HTTPS-capable permanent redirect from
   `lifthaul.com.ph` to `https://www.lifthaul.com.ph/`.

Afterward verify: apex resolution, apex HTTPS, a single permanent redirect to `www`, `www` HTTP 200,
certificate hostname/expiry, HSTS, no redirect loop, same-origin API calls, authentication cookies,
and that both health probes report the exact deployed SHA and deployment ID.

## Exact deployment and rollback procedure

1. Re-authenticate GitHub, push this branch and merge only after required checks pass.
2. Run the production workflow with the full SHA
   `771f02d7d666ecbc1e659329627de02c374ebb70` and required production reviewers.
3. Deploy that SHA to Railway; do not deploy the uncommitted user asset/prototype files.
4. Verify health/readiness identity, live asset hashes, database readiness and monitoring.
5. Run synthetic no-funds smoke tests through registration, login, fare estimate, booking, assignment,
   trip/POD, payment status and reporting. Do not use real customer data or real payments.
6. Record the new Railway deployment ID and retain build/test/smoke artifacts.

Rollback target before this release is the currently online Railway deployment
`fe901676-c522-427a-977f-b5ba43ab8f47`. Stop new deployments, record the incident, roll back the
application image, do not automatically reverse database migrations, verify health and a synthetic
read-only journey, and restore data only into an isolated target before any approved production
recovery.
