# LiftHaul MVP execution update — 14 October 2026

## Evidence-backed status

- Canonical production: `https://www.lifthaul.com.ph`
- Deployed release: `69f6cd6aead9dfb45f021221f8bc21e8bd36b5d9`
- Railway deployment: `a4f1cf95-bb5b-4bda-9ec2-38f270cd5794`
- Readiness: HTTP 200, schema 27, exact release and deployment identities present
- Health: HTTP 200, production environment
- Scheduled production health monitor: passing on the deployed release
- Canonical smoke run: GitHub Actions `38572211697`
  - exact release, health, PSGC geography, public pages and access controls: **PASS**
  - offline no-secret QA contract: **PASS**
  - protected authenticated QA: **BLOCKED** because the dedicated production QA credentials are absent
- Live collection, refunds, custody and payouts: **disabled and outside the no-live-funds MVP gate**

Formal acceptance remains **3 of 9 hard gates (33.3%)**. A healthy deployment does not close
authenticated UAT, recovery, commercial, contact-governance or live-money certification gates.

## Completed in this increment

- [x] Reconciled GitHub `main`, production `/healthz`, production `/readyz`, schema and deployment ID.
- [x] Re-ran the exact-SHA canonical public smoke against the deployed release.
- [x] Confirmed the authenticated job fails closed before login when protected QA secrets are missing.
- [x] Confirmed Railway has a production-scoped project token record; no secret value was exposed.
- [x] Added an explicit `verify_existing` exact-release mode for an authorized manual Railway deployment.
- [x] Added live deployment identity binding so acceptance cannot run against a different SHA or an
  unrecorded deployment.
- [x] Kept Railway mutation mode as the default and required its returned deployment ID to equal the
  live `/readyz` deployment ID.
- [x] Added deployment-contract regression coverage.

## Verification for this increment

- Deployment/startup contract: **17/17 passed**.
- Production QA and regional booking harness contracts: **9/9 passed**.
- Complete backend discovery: **1,528 tests**. The managed Windows sandbox ran 1,523 tests
  successfully and denied temporary-file creation for the five static-frontend routing tests; the
  same five tests then passed **5/5** in the permitted environment. No assertion failure was found.
- Workflow YAML parsing and `git diff --check`: **passed**.

## Current blocking actions

1. Provision a dedicated least-privilege `public_intake_qa` production identity and store its email and
   password only in the protected GitHub `production` environment as
   `LH_PROD_TEST_ADMIN_EMAIL` and `LH_PROD_TEST_ADMIN_PASSWORD`.
2. Replace or correct the invalid `RAILWAY_PROJECT_TOKEN` secret after a production-scoped token is
   created and copied directly from Railway to GitHub. Until then, use `verify_existing` only for a
   separately authorized, exact-SHA manual deployment.
3. Execute and evidence PostgreSQL backup/restore and application rollback on an isolated restore
   target; never run the destructive restore drill on the production database.
4. Obtain accountable role UAT and owner approvals for support/privacy contacts and the no-live-funds
   commercial trial boundary.

## Next release sequence

1. Merge this workflow hardening only after complete CI passes.
2. Configure the protected QA identity, then dispatch the exact release workflow in
   `verify_existing` mode for the exact deployed SHA.
3. Retain the redacted regional booking evidence artifact and link it to MVP Issue #20.
4. Close hard gate 4 only if all three bookings are trackable, present in the scoped intake queue and
   persistent across independent hosted reads.
5. Continue with hosted persona UAT and isolated recovery/rollback evidence. Do not activate live
   funds as part of these tests.
