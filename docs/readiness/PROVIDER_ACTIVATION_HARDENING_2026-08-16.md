# Provider Activation — Production Hardening (2026-08-16)

Checkpoint accepted by the owner: `5974a7a` (public Service Provider Registration → Credential →
OTP → Carrier Workspace). Registration is **closed as complete**; this increment is production-activation
hardening only — no new registration features.

## 1. OTP boundary — fail closed

The one-time contact code may only ever be surfaced in an **explicitly-declared** development/test
environment. Anything else — `production`, `staging`, an unknown value, or a missing `APP_ENV` — is
treated as production for every security decision.

| Rule | Implementation | Test |
|---|---|---|
| `dev_code` NEVER returned in production | `public_provider._dev_code_allowed()` = `APP_ENV ∈ {development,dev,local,test,testing}`; else False | `test_production_never_returns_dev_code_and_stays_pending` |
| Ambiguous / missing env ≠ development | unknown/empty `APP_ENV` → `_is_production()` True | `test_unknown_env_is_treated_as_production`, `test_missing_env_is_treated_as_production` |
| `dev_code` NEVER logged | plaintext code is never written to `audit_logs` or any log line | `test_code_never_written_to_audit` |
| Provider unavailable ⇒ delivery unavailable, account stays PENDING | `_deliver_code` returns `VERIFICATION DELIVERY UNAVAILABLE`; user stays `PENDING_VERIFICATION` | `test_production_never_returns_dev_code_and_stays_pending` |
| Startup guard for ambiguous env | `server._guard_env_posture()` logs the resolved posture and warns on an unrecognised `APP_ENV` | boot log: `env posture: app_env=… dev_code_allowed=… treated_as_production=…` |

**Testability without leaking:** an in-process capture seam (`OTP_TEST_CAPTURE=1` +
`public_provider.peek_code`) lets the acceptance harness read the code. It has **no HTTP route**, is off
by default, and is unset in real production.

## 2. Contact-verified ≠ company-verified (UI boundary)

`provider.html` states the distinction explicitly on the success screen:

> **Account verified. Company compliance review pending.** You may now complete your fleet profile, but
> marketplace assignments remain disabled until required compliance checks are approved.

with the separated states `✓ Email/mobile verified` vs `◻ KYB` / `◻ LTFRB/CPC` / `◻ Insurance` /
`◻ Marketplace eligible`. Verified live in the browser: the workspace dashboard shows
`Marketplace BLOCKED — carrier business not verified (KYB=NONE)`.

## 3. Production acceptance lifecycle (the owner's exact sequence)

`scripts/go_live/provider_activation_e2e.py` runs **in production posture** against `DATABASE_URL`:

```
Public Provider Registration → Company Application → Username/Password → Account PENDING
→ OTP issued → OTP verified → User ACTIVE → Normal /login → Own Carrier Workspace
→ Add Vehicle → Add Driver → Pair Driver/Vehicle → Add Service Area → Upload Compliance
→ (provider CANNOT self-verify) → Independent Reviewer Verification → Marketplace Eligibility
→ Cross-provider isolation (Provider B never sees/mutates Provider A)
```

**Result: 23/23 passed (sqlite locally).** Wired into `.github/workflows/go-live-postgres.yml` so it runs
against a **real PostgreSQL 16** service container on every push — the honest stand-in for a hosted DB.

Key facts the harness proved (not assumed):
- login is **blocked** before verification, **allowed** after;
- the verified login resolves to its **own** carrier only (bound principal; client `carrier_id` ignored);
- a provider adds DRAFT fleet/drivers/areas and **submits** compliance docs but **cannot self-verify**
  them (independent staff maker/checker required);
- a unit is **not marketplace-eligible** until independent verification + activation;
- providers share the **platform tenant**, so cross-provider isolation is enforced by **carrier binding**
  (portal `resolve_carrier` + carrier-scoped reads), verified via the portal surface.

## 4. Messaging provider — seam only, default OFF

`public_provider._provider_active(conn, channel)` is the single seam a real email/SMS provider plugs into
through the notification engine. It returns **False by default** — no provider is faked. Connecting a real
provider (owner action; credentials required) flips it on; until then production correctly reports delivery
unavailable.

## 5. Landing page + real map

The hero map was a hand-drawn stylised blob ("cartoon"). Replaced with a **real geographic outline** of the
Philippines (Mapsicon, MIT-licensed, recoloured to the brand palette), with Luzon/Visayas/Mindanao markers
placed from the actual path centroids. Provider CTAs on the landing page route into `provider.html`.

## 6. Portal envelope fix (found in passing)

Landing a provider in `portal.html` exposed a pre-existing bug: the portal's API client did not unwrap the
server's `{"data": …}` envelope, so the dashboard read `undefined.total` and showed `Carrier · #undefined`.
Fixed the client to unwrap `data` (matching `provider.html`) — this also repairs the portal's own
credential-login path. Verified live: dashboard now renders company/marketplace/vehicle/driver tiles.

## Still owner-blocked (correctly)
- **Real email/SMS provider** — needs an account + credentials (cannot be created here). Seam ready.
- **Hosted PostgreSQL / production host** — CI proves the lifecycle on real Postgres; a managed host is
  the owner's infra step.
