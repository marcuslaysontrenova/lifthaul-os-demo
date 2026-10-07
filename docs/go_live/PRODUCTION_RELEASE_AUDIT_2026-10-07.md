# LiftHaul Production Release Audit — 2026-10-07

**Decision: NO-GO for the new public release.**

The existing frontend and API are reachable, but the published frontend does not match the
tested working tree, the API does not expose a release SHA or deployment ID, and mandatory
hosted recovery, rollback, security, and post-deployment UAT evidence is incomplete. This is
not a statement that the existing host is down; it is a release-integrity decision.

## Verified current production surface

| Surface | URL | Evidence |
|---|---|---|
| Public frontend | `https://marcuslaysontrenova.github.io/lifthaul-os-demo/` | HTTP 200 on 2026-10-07 |
| API | `https://lifthaul-api-production.up.railway.app` | `/healthz` HTTP 200, `status=ok`, `env=production` |
| Database readiness | API `/readyz` | HTTP 200, `status=ready`, schema version 27 |
| Exact deployed identity | same probes | **Not available** on the current deployment |
| Future domain CORS | `https://www.lifthaul.com.ph` | **Not allowed** by the current API |

SHA-256 comparison of `index.html`, `driver-register.html`, `provider.html`,
`vehicle-catalogue.json`, and `config.js` found that every public file differs from the locally
tested candidate. The current public build therefore cannot be represented as this revision.

## Completed reversible engineering work

- Added release SHA and deployment ID to `/healthz` and `/readyz`.
- Added production startup guards that prevent live protected funds or Wise settlement from
  being enabled without the required provider mode, credentials, and approval gates.
- Kept all live payment, bank, e-wallet, card, QR Ph, payout, and Wise flags disabled in local,
  Compose, and Render templates.
- Added an exact-commit Railway deployment script using the documented `commitSha` mutation.
- Added a manual, production-environment GitHub workflow that rejects abbreviated, mismatched,
  or unmerged SHAs; runs regression/security/browser gates; deploys the exact backend commit;
  waits for matching release identity; runs no-funds production smoke; then publishes the same
  frontend commit to GitHub Pages.
- Added a 15-minute health workflow and evidence artifacts. It becomes operational only after
  the workflow is pushed and GitHub Actions notifications are configured.
- Added a cross-browser public UI gate for Chromium, Firefox, and WebKit at desktop, tablet, and
  mobile breakpoints. It checks the 34-record canonical catalogue, 34 distinct assets, one
  persistent selection, no horizontal overflow, canonical provider routing, and safe exits.
- Updated the stable hosting template to use paid, Singapore-region resources, private managed
  PostgreSQL, pooling, storage autoscaling, and manual deployment.

## Test evidence from the candidate working tree

| Gate | Result |
|---|---|
| Complete Python discovery suite | **PASS — 1,451 tests** |
| Synthetic HTTP business lifecycle | **PASS — 31/31 steps** |
| Public UI cross-browser UAT | **PASS — 18/18 tests** |
| Focused UI/deployment regression | **PASS — 54/54 tests** |
| Bandit production-module scan | **PASS — no qualifying high-risk result** |
| `pip-audit` against `requirements.txt` under Python 3.12 | **PASS — no known vulnerabilities** |
| SQLite destructive backup/restore/reconciliation drill | **PASS — matching fingerprint; 0.28 s synthetic RTO** |
| 40-client pooled concurrency/isolation test | **PASS — 40/40; 0 failures; 40 distinct carriers** |
| Hosted PostgreSQL backup/restore and PITR drill | **BLOCKED — credentials and isolated restore target required** |
| Production synthetic end-to-end smoke | **BLOCKED — exact deployment and test administrator secret required** |
| Independent penetration test | **BLOCKED — external assessor required** |

The test suite emitted pre-existing Python `ResourceWarning` messages for some unclosed test
sockets/files. They did not fail the suite but remain a Low-severity test-harness cleanup item.

## Release blockers

| Severity | Blocker | Required closure evidence |
|---|---|---|
| **Critical** | The new candidate is not deployed; local GitHub credentials are invalid, and the public assets differ from the candidate. | Re-authenticate GitHub, merge the candidate to `main`, run the exact-release workflow, and retain its immutable release artifact. |
| **Critical** | Production PostgreSQL persistence, automated backups/PITR, an isolated restore, reconciliation, and measured RTO/RPO are not independently verified. | Host-console backup policy plus a successful restore into a non-production database and reconciled counts/fingerprint. |
| **High** | The current API cannot identify its exact commit/deployment, so rollback targeting is unverified. | Deploy the health-metadata revision and record matching SHA/deployment ID. |
| **High** | Required Railway deployment and production synthetic-test secrets are not present in this environment. | Store the listed secrets in GitHub's production environment; never paste them into chat/source. |
| **High** | Monitoring and alert workflows exist locally but are not running from the repository. | Push, enable Actions, configure notification recipients, and capture a successful run plus a controlled alert. |
| **High** | No independent external penetration test and clean retest are on record. | Signed scope/report, Critical/High remediation, and clean retest. |
| **High** | Rollback is documented but not exercised against the hosted backend/frontend. | Controlled rollback rehearsal to a known SHA with health and data-compatibility checks. |
| **High** | `lifthaul.com.ph` is not yet owner-registered/verified; DNS, TLS, redirect, and future-domain CORS cannot be completed. | Domain ownership, GitHub Pages verification, DNS propagation, certificate, redirect, and CORS tests. |
| **High (payments only)** | Legal role, licensed provider contract, sandbox certifications, live webhook, payout/refund, pilot, and seven-day reconciliation evidence remain open. | Complete the Payment Production Certification Register. Live funds stay disabled meanwhile. |
| **Medium** | Hosted PostgreSQL load/capacity and connection-pool limits were not load-tested. | Approved production-like performance run, limits, alerts, and capacity decision. |
| **Low** | Test harness emits unclosed socket/file ResourceWarnings. | Close resources in affected tests and rerun with warnings promoted as agreed. |

## Owner actions required before the exact release workflow can run

1. Re-authenticate GitHub locally (`gh auth refresh -h github.com`) or reconnect the repository
   integration, then confirm permission to push commits and workflow files.
2. In the GitHub `production` environment, add least-privilege secrets:
   `RAILWAY_PROJECT_TOKEN`, `RAILWAY_SERVICE_ID`, `RAILWAY_ENVIRONMENT_ID`,
   `LH_PROD_TEST_ADMIN_EMAIL`, and `LH_PROD_TEST_ADMIN_PASSWORD`.
3. Set required reviewers for the `production` environment, protect `main`, require the regression,
   payment-security, and public-UI checks, and set GitHub Pages source to **GitHub Actions**.
4. In Railway, confirm `CORS_ORIGINS` includes the current Pages origin and—only after domain
   ownership—`https://lifthaul.com.ph,https://www.lifthaul.com.ph`.
5. Provide an isolated PostgreSQL restore target or authorize the host's restore workflow; do not
   expose `DATABASE_URL` in chat.

## Domain and DNS cutover (`lifthaul.com.ph`)

Do not create these records until the domain is purchased and added/verified in GitHub Pages.

| Name | Type | Value | TTL |
|---|---|---|---|
| `www` | `CNAME` | `marcuslaysontrenova.github.io.` | 3600 |
| `@` | `A` | `185.199.108.153` | 3600 |
| `@` | `A` | `185.199.109.153` | 3600 |
| `@` | `A` | `185.199.110.153` | 3600 |
| `@` | `A` | `185.199.111.153` | 3600 |
| `@` | `AAAA` | `2606:50c0:8000::153` | 3600 |
| `@` | `AAAA` | `2606:50c0:8001::153` | 3600 |
| `@` | `AAAA` | `2606:50c0:8002::153` | 3600 |
| `@` | `AAAA` | `2606:50c0:8003::153` | 3600 |

Add GitHub's domain-verification TXT record exactly as displayed in the repository owner's Pages
settings (normally `_github-pages-challenge-marcuslaysontrenova` with a GitHub-generated token).
Do not guess the token and do not create wildcard DNS records. Set the Pages custom domain to
`www.lifthaul.com.ph`; after DNS resolves, wait for GitHub's certificate, enable **Enforce HTTPS**,
and verify both apex-to-`www` redirect and `www` HTTPS. If restrictive CAA records exist, allow the
certificate authority GitHub Pages requires. Then validate API CORS from both origins.

## Rollback

1. Stop new deployments and record the incident timestamp/deployment ID.
2. In Railway, roll back to the immediately previous successful deployment; Railway restores that
   deployment's image and variables. Do not roll the database schema backward automatically.
3. Re-publish the last known-good full SHA through the exact-release workflow (or rerun its retained
   GitHub Pages artifact) so frontend and backend remain compatible.
4. Confirm `/healthz`, `/readyz`, release SHA, authentication, one synthetic read-only journey, and
   error rate. Keep all live funds disabled.
5. If data corruption occurred, restore PostgreSQL to an isolated target first, reconcile, obtain
   explicit incident approval, and only then perform a controlled production restore.

## Live financial capabilities

The software retains internal payment-status, fee, refund, dispute, and payout ledger controls, but
the following external capabilities remain disabled: escrow/custody, live Protected Payment,
GCash, Maya, QR Ph, cards, bank transfer, OTC collection, automated refund, provider payout, and
Wise administration-fee transfer. LiftHaul must not be described as BSP-approved, LTFRB-approved,
a licensed escrow provider, or payment-production-certified without documentary evidence.

