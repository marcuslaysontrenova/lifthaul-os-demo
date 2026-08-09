# LiftHaul OS — BSP Readiness Package

```
BSP OPS STATUS:
REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

> **LiftHaul is NOT BSP-registered.** This package is submission-preparation material. No claim of
> registration, licence, or BSP confirmation is made until BSP issues the appropriate
> registration/confirmation in writing. Until then `LIVE_PROTECTED_FUNDS_ENABLED=false` and no live
> customer funds move through the platform.

## Purpose

Prepare LiftHaul OS to (a) obtain a regulatory classification determination for its Protected
Payment feature under the **National Payment Systems Act (R.A. 11127 / NPSA)** and BSP's Operator of
Payment System (**OPS**) framework, and (b) support the selection and onboarding of a **BSP-regulated
payment provider** that performs the actual fund custody and settlement.

The platform's design intent is that **LiftHaul itself does not take custody of, hold, or commingle
customer funds.** A licensed provider holds funds; LiftHaul orchestrates and issues release
*instructions*. Whether that model requires LiftHaul to register as an OPS (or is exempt as a mere
technology/orchestration layer) is a **regulatory + legal determination**, not an engineering
assumption — see `01_OPS_APPLICABILITY_ASSESSMENT.md` and the counsel checklist.

## Contents

| # | Document | Directive item |
|---|---|---|
| 01 | OPS applicability assessment | applicability |
| 02 | Protected Payment operating model | operating model |
| 03 | End-to-end fund-flow diagram | fund-flow |
| 04 | Custody responsibility matrix | custody matrix |
| 05 | Payment-system architecture | architecture |
| 06 | Provider interface description | provider interface |
| 07 | Transaction state machine | state machine |
| 08 | Ledger & reconciliation design | ledger/recon |
| 09 | Fraud / risk controls | fraud/risk |
| 10 | Customer dispute / refund process | dispute/refund |
| 11 | KYB / KYC controls | KYB/KYC |
| 12 | Cybersecurity controls | cybersecurity |
| 13 | Incident response | incident response |
| 14 | Business continuity / DR | BC/DR |
| 15 | Privacy / data governance | privacy |
| 16 | Complaint handling | complaint handling |
| 17 | Provider due-diligence package | provider DD |
| 18 | BSP application document checklist | application checklist |
| 19 | BSP application field mapping (owner data) | application mapping (B) |

## Related artifacts already in the repo (reused, not duplicated)

- `docs/legal-payment/PROTECTED_PAYMENT_OPERATING_MODEL.md`
- `docs/legal-payment/PAYMENT_CUSTODY_RESPONSIBILITY_MATRIX.md`
- `docs/legal-payment/PROVIDER_DUE_DILIGENCE_CHECKLIST.md`
- `docs/legal-payment/COUNSEL_DECISION_CHECKLIST.md`
- Backend authority: `backend/protected_payment.py` (state machine, ledger, reconciliation,
  provider interface, certification harness), enforced by `backend/test_protected_payment*.py`.

## Three mandatory prerequisites before live funds

1. **Regulatory model confirmed** — BSP classification / counsel determination of the operating model.
2. **Licensed provider certified** — a BSP-regulated provider passes `certify_provider` + due diligence.
3. **Flags flipped** — `payments.legal_operating_model_approved` **and**
   `payments.licensed_provider_active` **and** `payments.live_protected_funds_enabled` all true.

The core LiftHaul platform (booking, matching, dispatch, trips, LTFRB carrier compliance, operator-
verified/external payment) may launch **without** these — see the go-live status in the final report.
