# LiftHaul Enterprise — RC1 Regulatory & Production Activation Program

> **Phase:** Regulatory & Production Activation (NOT feature development).
> **Engineering baseline:** FROZEN at commit `79f0fe0` — 866 tests passing, 0 failures.
> **Live protected funds:** OFF (`LIVE_PROTECTED_FUNDS_ENABLED=false`) and stay OFF until the legal +
> provider gate is satisfied.
> **Standing Orchestrator decision:** no more feature development for this release. Further coding
> only if BSP/counsel/provider integration or production deployment surfaces a genuine defect or a
> required integration change.

## Frozen-state attestation (verified 2026-08-09)

| Item | State |
|---|---|
| HEAD | `79f0fe0` |
| Full regression | 866 passed / 0 failed |
| `payments.live_protected_funds_enabled` | `false` |
| `payments.legal_operating_model_approved` | `false` |
| `payments.licensed_provider_active` | `false` |
| `marketplace.ltfrb_enforcement_enabled` | `false` (flip ON only after real CPCs verified) |
| `backend/marketplace_ops.py` | deleted (was untracked dead scaffolding — stays deleted) |
| LTFRB carrier-authority gate | integrated into `create_assignment` (CPC + unit + area) |
| BSP package | `docs/regulatory/bsp/` — application PREPARATION (not registered) |

## Responsibility legend

- **OWNER** — corporate facts, certifications, account/host provisioning, commercial decisions.
- **COUNSEL** — PH legal determinations (OPS applicability, operating model, terminology, consumer/DPA).
- **BSP / LTFRB / PROVIDER** — external regulators / the regulated payment provider.
- **ENG** — engineering execution (only where a real integration/defect requires it).

## Execution sequence (gated)

### Step 1 — Complete missing BSP corporate facts & certifications  · OWNER
Fill every `⛔ MISSING — OWNER MUST SUPPLY` field in
`docs/regulatory/bsp/19_BSP_APPLICATION_FIELD_MAPPING.md`: SEC no. + date, TIN, business address,
business permit, directors/officers, beneficial owners, capital, AML/CFT officer, DPO + NPC
registration, financial statements, board resolution, sworn certifications.
**Gate:** every owner field populated; nothing invented. **ENG action: none.**

### Step 2 — Counsel determines OPS applicability + payment-custody model  · COUNSEL
Answer `docs/legal-payment/COUNSEL_DECISION_CHECKLIST.md` and complete the decision record in
`docs/regulatory/bsp/01_OPS_APPLICABILITY_ASSESSMENT.md §5`. Fixes: OPS required (Y/N + category),
custody boundary, terminology ("Protected Payment" vs "Escrow"), go-live conditions.
**Gate:** signed determination on file. **ENG action: none** (controls already enforce whatever
counsel decides).

### Step 3 — BSP filing or pre-consultation  · OWNER + COUNSEL
Submit or pre-consult with BSP per Step 2, using the `docs/regulatory/bsp/` package (checklist in doc
18). **Gate:** BSP reference/acknowledgement received. Government-paced — not a one-day item.
**ENG action: none.**

### Step 4 — Select & certify a regulated payment provider  · OWNER + PROVIDER (+ ENG only to wire the adapter)
Run each candidate through `docs/regulatory/bsp/17_PROVIDER_DUE_DILIGENCE_PACKAGE.md` and the built
harness `certify_provider()`. A provider goes ACTIVE only when: **BSP-regulated status verified ∧
harness passes ∧ counsel approves**.
**ENG action (integration, not a feature):** implement the chosen provider's adapter against the
existing `ProtectedPaymentProvider` interface; no new engine. **Gate:** certification report
`active_eligible=true` for the selected, regulated provider.

### Step 5 — Verify real carrier CPCs, then enable LTFRB enforcement  · OWNER (verify) → ENG (flip)
Record + human-verify each carrier's LTFRB CPC against an official source
(`POST /admin/marketplace/ltfrb/authorities` → `.../verify` with a recorded source; never fabricated).
Then set `marketplace.ltfrb_enforcement_enabled=true` in production config.
**Gate:** carriers intended for live assignment have VERIFIED, unexpired CPCs; enforcement ON;
`create_assignment` hard-blocks invalid authority (already tested).

### Step 6 — Deploy production PostgreSQL environment  · OWNER (host) + ENG (deploy)
Provision PostgreSQL; set `DATABASE_URL`; deploy via `Dockerfile` / `docker-compose.yml` per
`docs/GO_LIVE_RUNBOOK.md`. Code is PostgreSQL-portable (portability guard green); this host could not
run a live PG server (env-blocked), so this executes on the owner's infrastructure.
**Gate:** app boots against PostgreSQL; `_seed_platform` initializes all schemas.

### Step 7 — Final PG-backed validation: migrations, backup/restore, smoke  · ENG + OWNER
Run the full suite against PostgreSQL; execute backup → restore rehearsal; run production smoke
(health, auth, RBAC `/me/permissions`, a booking→assignment path with LTFRB enforcement ON, the
Regulatory Compliance dashboard). **Gate:** all green; backup/restore proven.

### Step 8 — Launch LiftHaul Enterprise (live protected funds OFF)  · OWNER
Go live with external/operator-verified payments and `LIVE_PROTECTED_FUNDS_ENABLED=false`. Flip live
protected funds ON **only** after Steps 2 + 4 are both complete (legal model approved ∧ regulated
provider certified), which sets all three payment flags true.
**Gate:** platform serving; funds gate correctly OFF; three-flag rule intact.

## Go / No-Go for live protected funds (the hard gate — unchanged)

```
LIVE PROTECTED FUNDS = ON  ⇔  legal_operating_model_approved=true
                              ∧ licensed_provider_active=true
                              ∧ live_protected_funds_enabled=true
```
All three are `false` today. Enforced centrally by `assert_live_allowed()`; no code path moves live
funds otherwise.

## What is one-day-finishable vs. not

- **Finishable now (done):** frozen tested baseline, LTFRB enforcement, BSP application-prep package,
  provider certification harness, this activation program.
- **NOT one-day (external, government/commercial-paced):** BSP registration/classification, LTFRB
  approvals, provider onboarding, owner corporate facts, production host provisioning.

The priority is now singular: **file, certify, deploy, validate, go live** — not another code increment.
