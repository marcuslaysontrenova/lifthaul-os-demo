# LiftHaul — Provider Onboarding Acceptance Evidence

**Date:** 2026-08-23 · **Posture:** `APP_ENV=production` · **Backend:** real service layer

Two independent proofs of the exact owner-specified 17-step onboarding path.

## 1. In-process governance acceptance — `scripts/go_live/provider_activation_e2e.py`

Runs every step as canonical domain calls in production posture (OTP never leaked over
HTTP; obtained only via the in-process capture seam). **Result: 33 passed, 0 failed.**

Owner's path → asserted step:

| # | Owner step | Acceptance assertion |
|---|---|---|
| 1 | Register Provider | Public registration → company application + PENDING login |
| 2 | Create username/password | Canonical credential created; no code leaked in prod |
| 3 | OTP verify | Code issued via seam → verified → user ACTIVE |
| 4 | Login | `/login` blocked before verify, succeeds after |
| 5 | Carrier workspace | Token resolves to OWN carrier (bound principal) |
| 6 | Add vehicle/equipment | Self-service unit lands DRAFT |
| 7 | Confirm classification | Classified to canonical variant (`6-Wheeler Closed Van - 4T Class`) |
| 8 | Confirm accreditation fee | Server-authoritative assessment + transparent breakdown |
| 9 | Upload LTFRB/CPC | `AUTHORITY_TO_OPERATE` submitted (submit ≠ verify) |
| 10 | Upload OR/CR | `VEHICLE_REGISTRATION` submitted |
| 11 | Upload vehicle insurance | `INSURANCE` submitted |
| 12 | Upload cargo insurance | Cargo-insurance certificate submitted; independent review only |
| 13 | Add driver | Self-service driver created |
| 14 | Pair driver + vehicle | Compatibility check |
| 15 | Set service area | Service area added |
| 16 | Independent compliance review | Maker/checker SoD: provider CANNOT self-verify; reviewer verifies + activates |
| 17 | Confirm marketplace eligibility | Not eligible before review / payment ≠ approval; eligibility reflects independent verification after |

Plus governance guards proven, not assumed: provider cannot self-verify vehicle,
document, cargo insurance, or activate its own carrier; a carrier can never pay its own
fee; PAID ≠ eligible; and full **cross-provider isolation** (Provider B cannot see or
write to Provider A's records; a client-supplied `carrier_id` is ignored).

```
ACTIVATION RESULT (sqlite): 33 passed, 0 failed
```
> The identical script runs against real PostgreSQL by setting `DATABASE_URL` — it is the
> production/CI acceptance gate.

## 2. Live over-the-wire smoke — real HTTP server on :8787

Exercises the actual routes + auth middleware (not just domain functions):

| Step | Request | Result |
|---|---|---|
| Register | `POST /public/providers` | `status=VERIFY_CONTACT`, `carrier_id=1` |
| OTP verify | `POST /public/providers/verify` | `status=ACTIVE`, `redirect=portal.html` |
| Login | `POST /login` | bearer token issued |
| Workspace | `GET /portal/carrier/overview` (authed) | real carrier record returned |
| Add vehicle | `POST /portal/carrier/vehicles` | `vehicle_id=1`, `status=DRAFT`, **accreditation auto-assessed ₱894.88** |
| Accreditation | `GET /portal/carrier/accreditation` | transparent components (799 subtotal + 12% VAT), discount label `1–9 units` |
| Fleet | `GET /portal/carrier/fleet` | unit listed, `eligible=false` with coded reasons |

Conclusion: the backend logic **and** the live HTTP surface both pass the full
onboarding acceptance. The only remaining work to make the **public** links real is
hosting the backend + pointing `config.js` at it — see `BACKEND_HOSTING_RUNBOOK.md`.
