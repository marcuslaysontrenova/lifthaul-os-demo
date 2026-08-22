# Provider Registration · Fleet Accreditation · Commercial Fee Engine · Cargo Insurance Compliance

Enterprise increment completing the paid provider-accreditation lifecycle on top of the existing LiftHaul
baseline. **Extend-only** — no new carrier/vehicle/driver/fleet/KYB/LTFRB/compliance/claims/payment/
booking/document domain was created. New capability plugs into the canonical domains.

## Commercial model (as required)

```
COMPANY REGISTRATION      FREE
VEHICLE / EQUIPMENT       PAID one-time accreditation (server-priced by canonical variant)
CARGO INSURANCE           provider-uploaded compliance document (LiftHaul never underwrites/prices)
INSURANCE PROCESSING      DISABLED (goods_protection processing gated off; upload-only)
MARKETPLACE ACTIVATION    only after payment + INDEPENDENT compliance pass — payment is never approval
```

A unit can be `Fee: PAID · Compliance: PENDING · Marketplace: NOT ELIGIBLE`. Only independent verification
produces `Compliance: VERIFIED · Marketplace: ELIGIBLE`. Proven on the real stack (E2E below).

## What shipped (new, reuse-first)

| File | Purpose |
|---|---|
| `backend/accreditation.py` | Fee engine: master-data `accreditation_schedule` (per canonical VARIANT/CATEGORY, effective-dated, versioned, tenant-aware), `accreditation_volume_tiers`, immutable `accreditation_assessments` snapshot. `assess_fee` (server-authoritative — ignores client fee/variant), `record_payment` (finance; payment≠approval), `waive_fee`, `refund`, `fee_breakdown`, admin `set_fee`/`set_volume_tier`/`list_schedule`. Fees seeded from §16 as **data** (never hardcoded logic); specialized cranes → `MANUAL_QUOTE`. VAT + volume discount configurable. |
| `backend/cargo_insurance.py` | Cargo Insurance Compliance: provider `upload` → `SUBMITTED` → independent `review` (VERIFY/REJECT) → expiry monitoring → `eligibility_gate`. States per spec; **separate from vehicle insurance** (vehicle insurance can never satisfy it). Not an insurance product — no quote/price/bind/claims. Config-gated (`cargo_insurance.required`, default off). |
| `backend/fleet_registration.py` | `register_unit` now auto-assesses the accreditation fee from the canonical classification. `unit_eligibility` adds `ACCREDITATION_FEE_UNPAID` (config `accreditation.gate_enabled`) + the cargo-insurance gate — both independent of the existing KYB/LTFRB/doc/driver gates. `unit_readiness` adds "Accreditation Fee" + "Cargo Insurance" checklist lines. |
| `backend/server.py` | Routes: assess / breakdown / pay / waive / refund / schedule / volume-tiers; cargo-insurance upload / review / summary / expiring-queue. |
| tests | `test_accreditation.py` (17), `test_cargo_insurance.py` (9). |

## Payment ≠ approval (independent statuses)

`unit_eligibility` returns coded reasons; the accreditation-fee gate and the cargo-insurance gate are two
**independent** gates among KYB/LTFRB/OR-CR/vehicle-insurance/driver/service-area. Paying the fee clears
only `ACCREDITATION_FEE_UNPAID`; it never clears compliance. Verified in `test_paid_does_not_grant_eligibility`.

## RBAC / SoD (server-enforced)

- Carrier may register/add/upload/pay-initiate/view — **cannot** pay-confirm, waive, refund, change the
  fee schedule, or self-verify cargo insurance (all raise `ForbiddenError`).
- Finance (`payment.*`) confirms payment/refund. Platform admin (`*`) manages the fee schedule/waivers.
- Independent reviewer (`marketplace.insurance.manage`) verifies cargo insurance.

## Acceptance E2E (production posture, real stack)

`scripts/go_live/provider_activation_e2e.py` — **33/33** — now includes:
enable gates → **assess** (server-authoritative) → unpaid **NOT eligible** → **finance pays** →
**still NOT eligible (payment ≠ approval)** → provider **uploads cargo insurance** → provider **cannot
self-verify** → **independent verify** → fee & cargo gates cleared (remaining gates stay independent) →
independent vehicle review + activation → cross-provider isolation. Wired into CI (`go-live-postgres.yml`)
against real PostgreSQL.

## Honest status of UI surfaces

Backend + JSON API + governance + tests are complete for the fee engine and cargo-insurance compliance.
The **Provider Portal commercial view** (§30) and **Administration → Provider Commercial Configuration**
(§31) are exposed as API endpoints; the dedicated portal/admin **HTML screens are not yet built** in this
increment (API-complete, UI-pending) — flagged plainly rather than claimed done.
