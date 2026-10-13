# LiftHaul — protected production QA acceptance handoff

**System of record:** [MVP acceptance #20](https://github.com/marcuslaysontrenova/lifthaul-os-demo/issues/20)  
**Environment:** `production` in GitHub Actions and Railway canonical custom domain `https://www.lifthaul.com.ph`  
**Safety:** no direct database edits; no credential publication; no live funds; no booking dispatch.

## Prerequisite checks
1. Confirm Railway `lifthaul-api` deployment is **SUCCESS** and `/readyz` reports the intended 40-character merged commit. The GitHub workflow must pin the **same** deployed SHA before starting production QA.
2. Confirm the public PSGC manifest and all three island datasets are available, with a passing read-only canonical smoke job.
3. Confirm the scoped roles `public_intake_qa` and `public_intake_operator` are deployed and their database role grants seeded by startup. The test uses **only** `public_intake_qa`.

## Owner-controlled setup (human-only credential boundary)
An existing authorized **user administrator**, using their own managed identity, performs these steps. No assistant, CI job, developer, or client should request the password in chat, GitHub Issues, logs, or commits.

1. Sign in to `https://www.lifthaul.com.ph/console.html`. Navigate to **Administration Center → People & Access → Create User** (or the equivalent user-administration tab shown for your role).
2. Create a new *dedicated, auditable and controlled* user with a valid inbox controlled by the operator, an independently generated strong unique password, and role **Public Intake QA (Read Only)** (`public_intake_qa`). Leave the account active. Do not use an administrator or Founder account. Do not reuse the former shared invite password.
3. Confirm its role has **only** `marketplace.booking.view` and `marketplace.public_intake.view` and no write, user administration, finance, payout, or Founder permission. This QA account is restricted to inspecting the platform-owned PUBLIC_MARKETPLACE intake. This QA does not require MFA exemptions.
4. In GitHub repo Settings → Environments → `production` → Environment secrets, add `LH_PROD_TEST_ADMIN_EMAIL` and `LH_PROD_TEST_ADMIN_PASSWORD` for that dedicated QA account **directly in the GitHub UI**. These values are secret; never put them in a GitHub variable, issue, commit, screenshot, terminal transcript, or chat. Use the existing environment's protection/approval policies.
5. If the QA account is not needed after acceptance, disable it and remove the protected credentials using the same approved administrative access.

## Automated live acceptance
- Use the PR's **LiftHaul canonical production read-only smoke** workflow, or the merged/approved successor, against the exact deployed SHA. This workflow declares `environment: production` on the authenticated job.
- The job first verifies `/readyz` identity and public PSGC. Only then `scripts/go_live/prod_auth_qa.py` performs one authenticated `/login`, `/me/permissions`, authorized guest queue read, and checks that synthetic booking reference `LH-XDMVQO` appears. No public booking creation is part of this authenticated job.
- Denied access is required for booking-review **writes**, user administration, and Founder-only endpoints. The script uses nonexistent booking id `0` for the write-denial probe, so it cannot modify a real booking even if authorization regressed.
- The run must finish **PASS** with a verifiable GitHub Actions run ID and non-secret evidence. If it fails on missing secrets or absence of the synthetic reference, record the truthful blocker. Do not loosen RBAC or fabricate data.
- Collect separate sign-offs by an authorized tester for client, driver, fleet owner, operations, finance and Founder with MFA. Isolated test suites alone do **not** establish successful production sign-in for each persona.

## MVP disposition
Treat technical booking MVP, authenticated production MVP, and **live-money readiness** as separate gates. The last remains **NO-GO** without external PSP/legal certification, approved reconciliation, payout/refund governance and supported funds-flow controls. Never equate read-only QA acceptance with permission to collect or distribute funds.

**Report in #20:** deployed SHA, Railway deployment ID, canonical smoke run, authenticated QA run, each role's evidence, rollback proof, outstanding defects and accountable sign-offs. Only record **MVP GO — no live funds** once all required no-funds functional and operational gates have passed.
