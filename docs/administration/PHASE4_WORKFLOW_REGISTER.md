# PHASE 4 — Workflow Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** governed, versioned Workflow Administration over the existing state machines,
approval logic, and SLA/escalation surfaces.
**Method:** full inspection of `backend/core.py`, `backend/ops.py`, `backend/policy.py`,
`backend/crm_admin.py`, `backend/org.py`.

> Guiding rule (from the directive): **purely technical infrastructure states are NOT
> tenant-editable workflows.** The existing state machines are imported into governed
> workflow *definitions* that reproduce current behavior; the hard-coded transition guards
> remain the enforcement backstop. Phase 4 adds a governance/versioning/approval/SLA layer
> ABOVE them without weakening them.

## Classification legend

`GOVERNED` already admin-configurable · `HARDCODED` literal state machine in code ·
`PARTIALLY GOVERNED` policy-driven but states fixed · `SYSTEM CONTROLLED` technical/engine state ·
`LEGACY` · `DUPLICATED` · `UNUSED` · `NOT APPLICABLE`.

---

## A. Commercial workflows

| Workflow | Current states | Transitions | Code location | Approvals | Permissions | Org scope | SLA | Escalation | Audit | Hardcoded? | Versioned? | Migration risk | Recommendation | Class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| booking lifecycle | REQUEST_RECEIVED→UNDER_REVIEW→…→CONFIRMED (13) | `core._BOOKING_FLOW` | `core.py:413` | none inline | booking.review/ready | tenant/branch | none | none | booking.transition | YES | no | low (additive) | import as governed def `commercial.booking`; keep guards | HARDCODED |
| quotation lifecycle | draft→pending_approval→approved→sent→accepted/revision/declined/expired/superseded | `core.quotations.status` | `core.py:105,529` | policy threshold | quotation.submit/approve | tenant | none | none | quotation.* | YES | no | low | governed def `commercial.quotation` | HARDCODED |
| quotation approval | pending_approval→approved | `core._needs_approval` + `policy.evaluate_approval` + SoD | `core.py:515,547` | amount + discount threshold, credit-hold force, SoD | quotation.approve | tenant | none | none | quotation.approve | PARTIAL (policy-driven, states fixed) | no | low | governed **approval matrix** (amount/discount/credit/scope) | PARTIALLY GOVERNED |
| quotation acceptance | sent→accepted | `core.accept_quotation` | `core.py` | customer accept (SoD vs approver) | quotation.* | tenant | none | none | yes | YES | no | low | governed def transition | HARDCODED |
| customer onboarding | customers.status ACTIVE + credit_status | `core.create_customer`, `crm_admin` | `core.py:361` | none | customer.create | tenant | none | none | create | LEGACY (implicit) | no | low | governed def `commercial.customer_onboarding` | LEGACY |
| customer merge | POSSIBLE_DUPLICATE→…→MERGED | `crm_admin.py` | Phase 3 | merge.execute | crm.admin.merge.execute | tenant | none | none | CUSTOMER_MERGED | GOVERNED (Phase 3) | no | none | reference in matrix; keep Phase-3 controls | GOVERNED |
| commercial exception approval | — | none | — | — | — | — | — | — | — | NOT APPLICABLE (new) | — | — | new governed workflow domain | NOT APPLICABLE |

## B. Payments & finance workflows

| Workflow | Current states | Code location | Approvals | Class | Recommendation |
|---|---|---|---|---|---|
| payment verification | REQUEST_CREATED→LINK_SENT→…→VERIFIED | `core.payment_requests.status` | finance verify (SoD) | HARDCODED | governed def `finance.payment_verification` |
| refund approval | refund status + approve_refund | `ops.py:436` | refund approver | HARDCODED | governed def + approval matrix (amount) |
| expense approval | SUBMITTED→APPROVED/REJECTED | `ops.py:321` | expense approver | HARDCODED | governed def `finance.expense_approval` + matrix |
| invoice approval | ISSUED→PARTIALLY_PAID→PAID/OVERDUE | `ops.py:68,416` | finance | HARDCODED | governed def `finance.invoice_approval` |
| change order approval | DRAFT→…→ACCEPTED/BILLED | `ops.py:56,287` | approver | HARDCODED | governed def + matrix (amount) |
| credit exception | credit_policies + evaluate_credit | `crm_admin.py` (Phase 3) | evidence-only default | PARTIALLY GOVERNED | governed exception approval workflow |
| financial configuration approval | Phase-2 config set (audited) | `admin_platform.set_config` | admin.configuration.* | PARTIALLY GOVERNED | governed approval workflow gating high-risk config |

## C. Operations workflows

| Workflow | Current states | Code location | Class | Recommendation |
|---|---|---|---|---|
| job activation / execution | CONFIRMED→PLANNING→…→CLOSED (15) | `ops.JOB_FLOW` `ops.py:213` | HARDCODED | governed def `operations.job` (import 15 states + guards) |
| resource reservation | TEMP→CONFIRMED→RELEASED | `ops.reservations.status` | HARDCODED | governed def `operations.reservation` |
| dispatch | READY_FOR_DISPATCH→DISPATCHED (evidence-gated) | `ops.transition_job` | HARDCODED | governed def transition w/ evidence requirement |
| maintenance | OPEN→DONE (+equipment MAINTENANCE) | `catalog.py` | HARDCODED | governed def `operations.maintenance` |
| inspection | result PASS/FAIL | `catalog.py` | HARDCODED | governed def `operations.inspection` |
| safety incident | safety_records result | `ops`/`catalog` | HARDCODED | governed def `operations.safety_incident` + severity matrix + SLA |
| equipment release | equipment.status ACTIVE/MAINTENANCE | `catalog.py` | SYSTEM CONTROLLED-ish | governed transition; keep effect logic |

## D. SYSTEM CONTROLLED (NOT converted to tenant-editable workflows)

| Item | Location | Why not a tenant workflow |
|---|---|---|
| session lifecycle, schema_version, correlation propagation | `core`, `db` | technical infrastructure |
| payment provider callback state (MockWise) | `core.MockWiseProvider` | external integration engine state |
| equipment MAINTENANCE toggle side-effect | `catalog.open/close_work_order` | a side-effect of the maintenance workflow, not a user-editable path |
| DB transaction/commit state | adapters | engine-level |

## E. Existing approval / SLA / escalation surfaces

| Concern | Present today? | Notes |
|---|---|---|
| Approval thresholds | YES (Phase 2) | `policy.evaluate_approval` amount+discount, credit-hold force; **Phase 4 generalizes into approval matrices** |
| Separation of duties | YES | `core.CONFIG.separation_of_duties`, `admin_platform.SOD_PAIRS`; **preserved + enforced in workflow approvals** |
| SLA | NO | none today → **new** SLA administration (duration/calendar/warning/breach/pause/resume) |
| Escalation | NO | none today → **new** escalation paths (user/role/manager/branch/BU/group) |
| Delegation | NO | none today → **new** governed delegation (cross-tenant/circular/expiry blocked) |
| Working/holiday calendars | YES (Phase 1 C-004) | `org.effective_working_calendar` + `org.effective_holidays` → **reused by the SLA calculator** |

## Summary counts

- **HARDCODED state machines → imported as governed definitions:** 12 (booking, quotation,
  acceptance, payment verification, refund, expense, invoice, change order, job, reservation,
  maintenance, inspection, safety incident).
- **PARTIALLY GOVERNED → generalized:** quotation approval, credit exception, financial-config approval.
- **GOVERNED (kept):** customer merge (Phase 3), Phase-2 policy cascade.
- **NEW capabilities:** approval matrices, SLA administration, escalations, delegations, workflow
  versioning + validation + simulation + instance engine.
- **SYSTEM CONTROLLED (untouched):** sessions, provider callbacks, engine/transaction state.

## Financial & operational safety commitments

1. Importing a state machine into a governed definition **reproduces current outcomes**; the existing
   hard-coded transition guards (`_BOOKING_FLOW`, `JOB_FLOW`, evidence gates, dispatch/safety gates)
   remain the enforcement backstop → **UNEXPECTED OPERATIONAL STATUS CHANGES = 0**.
2. Approval matrices default to the **exact Phase-2 thresholds**; nothing recomputes a financial value
   → **UNEXPECTED FINANCIAL DIFFERENCES = 0**.
3. Workflow instances are **additive** metadata bound to a version; existing open transactions are
   NOT moved onto new versions (they stay on legacy behavior) → no operational drift.
4. Simulation is strictly non-mutating (no records, no notifications, no sequence consumption).
5. Active/published versions are **immutable** (checksum-stamped); edits require a new draft version.
