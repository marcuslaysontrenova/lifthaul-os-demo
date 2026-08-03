# PHASE 10 — SaaS Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-03
**Scope:** introduce the governed SaaS commercial layer WITHOUT fabricating any contract, price,
subscription, or payment status, and WITHOUT changing any financial value, operational status,
entitlement, or tenant access for existing tenants.

## Migration strategy (additive, non-fabricating, entitlement-preserving)

The Phase-10 layer is **additive**: a governed catalog + subscriptions + entitlements sit above the
existing tenant/module/flag surfaces. Existing tenants keep their enabled modules (Phase-6
`module_tenant_status`), feature flags, users, data, and financial records. No contract, pricing, or
payment status is invented. A neutral internal subscription is created **only where justified** and
carries an explicit migration origin.

| Existing surface | Migration action | Impact |
|---|---|---|
| tenants (RGO + any pilots) | left as-is; a `LEGACY_INTERNAL` subscription may be attached (no fabricated price/contract) | none — access unchanged |
| module enablement (Phase 6) | **preserved** — entitlements read/write the SAME `module_tenant_status` (no parallel store) | none — modules unchanged |
| feature flags (Phase 6) | **preserved** | none |
| freight invoices / payments (Phase 7) | kept DISTINCT from SaaS subscription fees | none |
| tax policy (Phase 2) | **reused** for subscription billing (no duplicate tax logic) | none |

## Classification of existing tenants / controls

| Class | Handling |
|---|---|
| internal tenant | RGO (Tenant Zero) — neutral internal subscription only if justified; access unchanged |
| pilot tenant | attach a governed subscription with recorded migration origin (no fabricated commercial terms) |
| production tenant | requires real governed commercial evidence to activate (never fabricated) |
| no subscription | left without a subscription rather than inventing one |
| deterministic plan mapping | applied only where a real plan maps cleanly |
| manual commercial mapping | flagged for a human commercial decision |
| excluded / historical | untouched |

## Migration results

| Metric | Result |
|---|---|
| Existing tenants | preserved (access unchanged) |
| Fabricated contracts | **0** |
| Fabricated pricing | **0** |
| Fabricated subscriptions/payment status | **0** |
| Modules preserved | yes (Phase-6 store reused) |
| Feature flags preserved | yes |
| **Financial differences** | **0** |
| **Operational status differences** | **0** |
| **Entitlement losses** | **0** |
| **Tenant access changes** | **0** |

## Invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED OPERATIONAL STATUS CHANGES = 0
UNEXPECTED ENTITLEMENT LOSSES = 0
UNEXPECTED TENANT ACCESS CHANGES = 0
```

- **Financial:** SaaS billing evidence is a NEW, distinct artifact (Phase-2 tax); `test_saas_does_not_
  change_freight_financials` asserts a quotation's `tax`/`total` unchanged (72000/672000) after billing.
- **Entitlement / tenant access:** the entitlement layer reads/writes the SAME Phase-6 module store —
  existing enablement is preserved; `classify_existing` reports 0 entitlement loss and 0 access change.
- **RBAC preserved:** entitlement checks AUGMENT `core.require` (never replace) — a user without the
  RBAC permission is still denied (`test_entitlement_does_not_replace_rbac`).

## Reversibility

- Only additive DDL; no column drops; no existing tenant/module/flag mutated destructively.
- Subscriptions/plans/billing are additive rows; removing them affects no operational data.
- Downgrades are non-destructive (removed modules become read-only/archived; no data deleted).
