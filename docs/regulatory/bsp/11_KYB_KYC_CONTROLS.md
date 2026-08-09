# 11 — KYB / KYC Controls

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Authoritative: `backend/marketplace_trust.py` (KYB 10-state machine) + `marketplace_onboarding.py`
(document intake) + `backend/ltfrb.py` (carrier transport authority).

## KYB (carrier / shipper)

- 10-state KYB machine: submission → document verification → decision, with SEC/DTI + TIN + address +
  officers captured from submitted documents.
- **Never-fabricate adapters**: automated lookups return `MANUAL_VERIFICATION_REQUIRED` when no
  legally-accessible live registry API is configured; a human records source + evidence.
- Document expiry tracked; expired critical documents block activation.

## KYC (payer)

- Regulated **KYC of the paying customer is performed by the licensed provider** under its BSP
  obligations. LiftHaul shares identifying data and stores references, not regulated KYC verdicts.

## Carrier transport authority (LTFRB) — see doc set C/D

- CPC number, case reference, area of operation, authorized units, validity, port authority, special
  permits recorded and **human-verified against an official LTFRB source** (never fabricated).
- Hard assignment gate blocks unverified/expired CPCs, unauthorized units, out-of-area work when
  enforcement is enabled.

## Evidence

`backend/test_ltfrb.py`, trust suites, onboarding suites. Verification records store source, result,
verified_by, verified_at, evidence, expiry.
