# Exact-production acceptance-gate separation — 2026-10-14

## Root cause and disposition

Workflow 38582354600 proved the exact release SHA dfea0b9df01824a73bb1a39a0f611f5e3f6c0ba1,
Railway deployment 0ac737cf-5b1f-4709-8450-ba3e5631182b, and schema version 27.
The old preflight exited at module import when its LH_ADMIN_* variables were missing.
Therefore public read-only checks were not executed. The three-region synthetic journey
was skipped, and no regional booking artifact was generated. PR #31's identity binding worked.

## Independent evidence

- `preflight.py --scope public` needs NO secrets: GET healthz, GET readyz,
  exact SHA and deployment ID when supplied, HTTPS, anonymous queue denial,
  and CORS rejection of an untrusted Origin. It emits `public-preflight.json`.
  A successful public scope means PUBLIC_PASS_ADMIN_UNVERIFIED; it is NOT MVP GO.
- `preflight.py --scope admin` requires **separate** protected
  `LH_PROD_AUDIT_ADMIN_EMAIL` and `LH_PROD_AUDIT_ADMIN_PASSWORD`.
  After revalidating release identity, it logs in, checks the identity is
  administrator-level, reads governed platform configuration, explicitly verifies
  all safety-sensitive flag records exist exactly once and are false, logs out,
  and emits `admin-preflight.json`. Missing credentials, unrecognized role,
  unreadable config, absent flags, or activated flags cannot pass.
- The workflow preserves both available artifacts even when one check fails.
  It enforces the public, admin, identity, and regional QA outcomes in a final
  fail-closed step. Any missing, failed, or skipped result is NOT complete acceptance.

## Credential boundaries

The existing dedicated `public_intake_qa` credentials remain in the protected
GitHub `production` environment under `LH_PROD_TEST_ADMIN_EMAIL` and
`LH_PROD_TEST_ADMIN_PASSWORD`. The workflow continues to map these into the
regional synthetic acceptance script's legacy `LH_ADMIN_*` process variables,
**not** into the privileged configuration audit. That mapping does not grant
administrator authority. NEVER put real credentials in a commit, issue, log, or
unprotected variable. Authorized administrators must provision the distinct
production audit identity and protected secrets through the approved UI.

## Production safety

No production mutation is performed by preflight. It does not create a booking,
dispatch, change config, capture money, or invoke payouts. Regional acceptance
is explicitly gated on successful identity, public checks, and privileged audit;
it creates only its existing three documented synthetic no-funds bookings upon
a separately authorized production workflow run. Do not dispatch the workflow
merely to fill gaps without the required production approval.

## Remaining gate dependencies

1. Validate the new PR with required CI, review and merge under the normal process.
2. An authorized operator provisions the **separate** admin audit identity/secrets
   and dedicated least-privilege QA identity/secrets into protected environment secrets.
3. Rerun the exact approved SHA in verify_existing mode only if that SHA is
   actually deployed and the workflow has production authorization. Retain public,
   admin, and regional booking evidence and link the run to issue #20.
4. Complete persona-based authenticated UAT, role isolation, current isolated
   backup/restore and rollback proof, support/privacy accountability, commercial
   owner approvals, and sign-offs required by issue #20.

Production no-live-funds MVP remains NO-GO until all of issue #20's required
evidence and accountable sign-offs exist. Live collection/custody/refunds/payouts
remain a separate NO-GO until external PSP, legal and finance approvals.
