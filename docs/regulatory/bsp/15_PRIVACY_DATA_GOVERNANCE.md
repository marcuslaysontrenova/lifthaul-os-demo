# 15 — Privacy / Data Governance

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

Framework: **R.A. 10173 — Data Privacy Act (DPA)** and NPC issuances.

## Personal / sensitive data handled

| Data | Purpose | Location |
|---|---|---|
| Shipper/carrier corporate + officer info | KYB | `mkt_*` (business tables) |
| Driver identity + licence | Qualification | onboarding tables |
| Payment references + evidence | Protected Payment | payment/ledger tables |
| Payout account references | Settlement | payout tables (no full secrets) |
| Regulated KYC verdicts | Provider-side | **provider**, not LiftHaul |

## Principles

- **Data minimization** — LiftHaul stores references + evidence, not regulated KYC internals or full
  payment secrets.
- **Purpose limitation** — data used for booking, compliance, settlement only.
- **Access control** — RBAC + tenant isolation; audit ledger for access to sensitive actions.
- **Retention** — periods to be set by counsel/NPC guidance (doc 08 §retention).

## Roles (to be formalized with the owner)

- **Personal Information Controller (PIC):** LiftHaul entity.
- **Processor:** provider + hosting.
- Register with NPC, appoint a DPO, publish a privacy notice — owner/counsel actions.

## Subject rights

Access, correction, objection, erasure (subject to legal retention). Process to be documented in the
privacy notice.
