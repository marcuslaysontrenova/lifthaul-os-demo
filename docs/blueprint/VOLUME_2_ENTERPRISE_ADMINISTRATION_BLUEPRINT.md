# Volume 2 — Enterprise Administration Blueprint

> Administration is **not one screen — it is another ERP** (ED-002). This volume
> specifies every administrative capability, configuration screen, governance rule,
> and permission for **Platform 1**. It is the operating system of LiftHaul OS.
> Version 0.1 (DRAFT).

---

## 1. Design premises

- **Configuration-first (ED-004).** Each screen below turns a former code constant
  into administrator-owned data. The test for inclusion: *"Would an operator ever
  reasonably want this different without a developer?"* If yes, it belongs here.
- **Tenant-scoped.** Every setting is per-tenant (organization), with a platform
  default and optional business-unit/branch override (§9 Configuration Cascade).
- **Governed.** Every admin mutation emits an `audit_logs` event and is gated by a
  permission in the (soon data-driven) permission model.

## 2. The Administration ERP — canonical menu

```
Enterprise Administration
├── Organization
│   ├── Company Profile        (legal entity, tax IDs, logo, locale, currency)
│   ├── Branches               (physical yards/depots; dispatch origin points)
│   ├── Departments            (org units for approval routing & reporting)
│   ├── Business Units         (P&L segments)
│   ├── Cost Centers           (finance allocation targets)
│   └── Holidays               (per-branch calendars → SLA & scheduling)
│
├── Identity & Access
│   ├── Users                  (lifecycle: invite→active→suspend→offboard)
│   ├── Roles                  (data-driven; replaces core.PERMISSIONS)
│   ├── Permissions            (capability catalog; grant matrix)
│   ├── User Groups            (bulk role/scoping assignment)
│   ├── Sessions               (active sessions; force logout)
│   ├── MFA                    (policy + per-user enrollment)
│   ├── Password Policy        (length/rotation/complexity/lockout)
│   └── Login History          (audit of authn events)
│
├── Workflow Administration
│   ├── Booking Workflow       (states + transitions + guards)
│   ├── Approval Matrix        (thresholds × roles × amount bands)
│   ├── Dispatch Rules         (assignment, resource guards, conflict policy)
│   ├── Finance Rules          (payment %, verification, tax defaults)
│   ├── Escalation Rules       (time-based reassignment/notify)
│   └── SLA Rules              (response/resolution clocks by service/priority)
│
├── CRM Administration
│   ├── Customer Types         (segments)
│   ├── Industries             (master data)
│   ├── Territories            (geo → ownership & pricing)
│   ├── Credit Policies        (limits, terms, holds)
│   ├── Pricing Policies       (rate cards, discounts, surcharges)
│   ├── Portal Settings        (what customers can see/do)
│   └── Duplicate Rules        (match keys for dedupe)
│
├── Fleet Administration       (equipment classes, capacities, cert requirements, maintenance intervals)
├── Finance Administration     (chart of accounts, tax codes, payment methods, GL mapping)
├── Dispatch Administration    (shifts, crew skills, resource pools, geofences)
├── AI Administration          (models, prompts, confidence thresholds, override policy → Volume 1 P6)
├── Reporting Administration   (report catalog, scheduled exports, KPI definitions)
├── Notifications              (templates, channels, routing, quiet hours)
├── Integrations               (connector registry, credentials vault, webhook subscriptions)
├── Audit                      (immutable event browser, retention, export)
└── System Configuration       (feature flags surface, environment banners, limits registry)
```

## 3. Module specifications

Each module is specified with the same contract so implementation is mechanical and
governable. (Format: **Purpose · Config surface · Master data · Permissions ·
Workflow · Audit events · Depends on**.)

### 3.1 Organization → Company Profile
- **Purpose:** the tenant's legal + brand identity; root of the config cascade.
- **Config surface:** legal name, trading name, tax IDs, registered address, default
  currency, locale/timezone, fiscal year start, logo/brand tokens (→ Branding).
- **Master data:** currency, locale, timezone catalogs.
- **Permissions:** `org.view`, `org.manage`.
- **Workflow:** none (direct edit, audited).
- **Audit events:** `ORG_PROFILE_UPDATED`.
- **Depends on:** tenant dimension (G-01).

### 3.2 Organization → Branches / Departments / Business Units / Cost Centers
- **Purpose:** the org graph that approval routing, dispatch origin, and finance
  allocation all reference.
- **Config surface:** CRUD with hierarchy (branch↔department↔BU↔cost center), status,
  manager assignment, dispatch-origin flag (branches), GL segment (cost centers).
- **Permissions:** `org.structure.view|manage`.
- **Audit:** `ORG_UNIT_UPSERTED`, `ORG_UNIT_ARCHIVED`.
- **Depends on:** Company Profile.

### 3.3 Organization → Holidays
- **Purpose:** feed SLA clocks and scheduling with non-working days per branch.
- **Config surface:** calendar per branch; recurring + one-off; half-days.
- **Consumed by:** SLA Rules, Scheduling (P3), Escalation Rules.

### 3.4 Identity & Access → Users
- **Purpose:** full user lifecycle. **Extends existing `users` table** with
  `tenant_id`, `status`, `groups`, `mfa_enrolled`, `last_login_at`.
- **Config surface:** invite (email), assign roles/groups + scope (branch/BU),
  suspend, reset MFA, force password reset, offboard (soft-delete + session kill).
- **Permissions:** `iam.user.view|invite|manage|offboard`.
- **Workflow:** optional approval on privileged-role grant (Approval Matrix).
- **Audit:** `USER_INVITED|ACTIVATED|SUSPENDED|ROLE_GRANTED|OFFBOARDED`.
- **Migrates:** today's `core.create_user`.

### 3.5 Identity & Access → Roles / Permissions / User Groups (**G-02 fix**)
- **Purpose:** replace the hard-coded `core.PERMISSIONS` dict with a **data-driven**
  role→permission grant matrix an admin owns.
- **Model:** `permissions` (capability catalog, seeded from code inventory),
  `roles` (per-tenant, some system-locked), `role_permissions` (grant matrix),
  `user_roles`, `groups`, `group_roles`.
- **Config surface:** permission matrix editor (role × capability grid); clone role;
  system roles read-only; effective-permission preview for a user.
- **Permissions:** `iam.role.view|manage`, `iam.permission.view`.
- **Audit:** `ROLE_UPSERTED`, `ROLE_PERMISSION_CHANGED`, `USER_GROUP_CHANGED`.
- **Migration path:** seed `permissions` from the current code capability list; seed
  system roles (admin, estimator, approver, finance, fleet_manager, dispatcher) with
  their current grants; switch `require()` to read the grant matrix (cached per session).

### 3.6 Identity & Access → Sessions / MFA / Password Policy / Login History (**G-11**)
- **Sessions:** list/kill active sessions (extends `sessions` table with device,
  ip, last_seen). `iam.session.view|revoke`.
- **MFA:** policy (off/optional/required-by-role) + TOTP enrollment. `iam.mfa.manage`.
- **Password Policy:** min length, complexity, rotation days, reuse window, lockout
  threshold. Enforced in `security.py`. `iam.password_policy.manage`.
- **Login History:** append-only authn log (success/fail, reason, ip). Read-only;
  retention configurable. Feeds Audit.

### 3.7 Workflow Administration (**G-04 fix**)
- **Purpose:** move booking/approval/dispatch/finance/escalation/SLA logic from code
  into **configurable definitions** executed by a workflow engine (Volume 4 §Workflow Engine).
- **Booking Workflow:** states + allowed transitions + entry/exit guards + required
  fields per state. Seeded from today's booking/job stage machine.
- **Approval Matrix:** rows of `(scope, amount_band, required_role, sequence)`. Drives
  quotation approval (today's SoD), credit holds, privileged-role grants. Replaces the
  hard-coded ₱500k approval threshold with configurable bands.
- **Dispatch Rules:** resource-eligibility guards, double-book policy (today's
  reservation conflict = a rule), auto-assignment strategy.
- **Finance Rules:** downpayment %, verification requirement, tax defaults, allocation
  order. Replaces in-code 30% dp / 12% VAT constants.
- **Escalation Rules:** `if state X unchanged for N business-hours → notify/ reassign`.
- **SLA Rules:** response/resolution targets by service × priority; consumes Holidays.
- **Permissions:** `workflow.view|manage` (high-privilege; approval-gated changes).
- **Audit:** `WORKFLOW_DEFINITION_PUBLISHED` (versioned; never edited in place).

### 3.8 CRM Administration
- Customer Types, Industries, Territories, Duplicate Rules → **master data** (extends
  `master_data` categories). Credit Policies + Pricing Policies → new config tables
  consumed by Platform 2. Portal Settings → toggles for the customer portal (P2/P8).
- **Permissions:** `crm.admin.view|manage`. **Audit:** `CRM_POLICY_UPSERTED`.

### 3.9 Fleet / Finance / Dispatch Administration
- **Fleet:** equipment classes, capacity bands, required certifications, maintenance
  intervals → drives P3 reservations, inspections, maintenance work orders.
- **Finance:** chart of accounts, tax codes, payment methods, GL segment mapping →
  drives P4. Establishes finance independence.
- **Dispatch:** shifts, crew skills, resource pools, geofences → drives P3 scheduling.

### 3.10 AI Administration → see Volume 1 Platform 6 (specified in Volume 4 §AI Platform).

### 3.11 Reporting Administration / Notifications / Integrations / Audit / System Config
- **Reporting:** report catalog, KPI definitions, scheduled exports.
- **Notifications:** extends `notification_templates`; adds channels (email/SMS/push/
  webhook), routing rules, quiet hours. **Config-first** template editor.
- **Integrations:** connector registry + encrypted credential vault + webhook
  subscriptions (Volume 4 §Integration). `integration.manage` (high-privilege).
- **Audit:** immutable browser over `audit_logs` + `login_history`; retention + export.
  Read-only by design.
- **System Configuration:** the **Limits Registry** (every configurable numeric
  limit), environment banners, and the feature-flag surface (P9).

## 4. Configuration cascade (ED-004 mechanics)

Resolution order, most-specific wins:

```
Platform default  →  Tenant config  →  Business-unit/Branch override  →  User preference
```

Every configurable value is stored with a `scope` (`platform|tenant|unit|user`) and a
`scope_ref`. The resolver returns the highest-specificity value present. This is the
single mechanism behind "an administrator can configure this instead of code."

## 5. Permission catalog (seed)

Seed the `permissions` table from the current code capabilities (non-exhaustive,
derived from today's `require()` sites): `booking.create`, `quotation.create|submit|
approve|send`, `payment.request|verify`, `job.confirm|transition|safety`,
`change_order.create|approve`, `invoice.create|allocate`, `notification.create`,
`equipment.manage`, `fleet.manage`, plus the new admin capabilities defined above
(`org.*`, `iam.*`, `workflow.*`, `crm.admin.*`, `integration.*`, `report.*`). Volume 5
carries the full matrix as the traceability seed.

## 6. What this replaces in today's code

| Today (code constant / hard-coded) | Becomes (admin-owned data) |
|---|---|
| `core.PERMISSIONS` dict | Roles + Permissions + grant matrix (§3.5) |
| ₱500k approval threshold | Approval Matrix amount bands (§3.7) |
| 30% downpayment / 12% VAT | Finance Rules (§3.7) |
| Booking/job stage machine in code | Booking Workflow definition (§3.7) |
| Reservation double-book guard | Dispatch Rules (§3.7) |
| `notification_templates` (data ✓) + code channels | Notifications admin (§3.11) |
| `system_config` (data ✓, thin) | System Configuration + Limits Registry (§3.11) |
| `master_data` (data ✓) | Extended per CRM/Fleet/Finance categories |

## 7. Acceptance criteria for Platform 1 (gates to "Administration exists")

1. A second tenant can be created and fully isolated (no data bleed) — proven by test.
2. An admin can create a role, grant permissions, and a user with that role is
   enforced server-side **without a code change**.
3. An admin can change the approval threshold and a quotation routes accordingly —
   **without a code change**.
4. Every admin mutation appears in the Audit browser with actor, before/after, time.
5. Every configurable value resolves through the cascade (§4) with a visible source.
