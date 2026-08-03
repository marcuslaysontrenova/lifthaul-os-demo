# PHASE 10 — SaaS & Commercial Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-03
**Scope:** governed SaaS commercial layer — product catalog, plans, entitlements, subscriptions, usage
metering, quotas, overage, billing evidence, tenant provisioning, trials/renewal/suspension/termination,
marketplace commercial controls.
**Method:** full inspection of `admin_platform.py` (tenants), `settings.py` (Phase-6 modules + feature
flags + backup/retention), `tenant.py` (isolation), `org.py` (org units), `ai_admin.py` (AI budgets),
`policy.py`/`config_registry.py` (Phase-2 tax), `core.py`/`ops.py` (invoices/payments).

> Guiding rules (directive): a user needs BOTH the RBAC permission AND the tenant entitlement —
> **entitlement never replaces RBAC**; enforcement is **server-side** (not menu-hiding); quotas are
> **atomic**; metering is **idempotent**; published plan/pricing/billing snapshots are **immutable**;
> **reuse** the Phase-6 module registry + feature flags (no parallel module definitions) and the
> Phase-2 tax policy (no duplicate tax logic).

## Classification legend

`GOVERNED` · `FEATURE FLAG ONLY` · `MODULE CONTROLLED` · `HARDCODED` · `PARTIAL` · `COMMERCIAL DRAFT` ·
`LEGACY` · `UNUSED` · `NOT APPLICABLE`.

---

## A. Existing tenant / commercial surfaces

| Domain | Current source | Code | Tenant scope | Plan dep? | Quota? | Billing? | Class | Recommendation |
|---|---|---|---|---|---|---|---|---|
| tenant creation | `admin_platform.create_tenant(code,legal_name,...,plan="STANDARD")` | admin_platform.py:429 | yes | a bare `plan` string only | no | no | PARTIAL | keep as the low-level tenant row; wrap in governed provisioning |
| tenant status | `tenants.status` | admin_platform | yes | no | no | no | PARTIAL | drive from subscription status (active/suspended/terminated) |
| module enablement | `settings.modules` + `module_tenant_status` (Phase 6) | settings.py:478,506 | yes | no | no | no | MODULE CONTROLLED | **reuse** as entitlement backing store |
| feature flags | `settings.feature_flags` + overrides (Phase 6) | settings.py:413,443 | yes | no | no | no | FEATURE FLAG ONLY | **reuse** for feature entitlement toggles |
| AI budgets | `ai_admin.ai_budgets` (Phase 9) | ai_admin.py | yes | no | cost | no | GOVERNED | reference as an AI-cost quota input |
| invoices / payments | `core`/`ops` invoices, `payment_requests`, Wise (Phase 7) | core.py, ops.py | yes | no | no | freight only | GOVERNED (freight) | keep DISTINCT from SaaS subscription fees |
| tax / currency policy | `policy.evaluate_tax` + config (Phase 2) | policy.py | cascade | no | no | yes | GOVERNED | **reuse** for subscription billing tax snapshot |
| org units | `org` (Phase 1 C-004) | org.py | yes | no | no | no | GOVERNED | **reuse** for default-org provisioning |
| retention / backup | `settings` (Phase 6) | settings.py | yes | no | no | no | GOVERNED | reference in trial/termination data lifecycle |

## B. Absent today (NEW in Phase 10)

| Domain | Present? | Phase-10 action |
|---|---|---|
| product catalog | NO | new governed `products` |
| plans / editions | NO (only a `plan` string on tenants) | new immutable `plan_versions` |
| pricing versioning | NO | immutable pricing on plan versions |
| subscriptions | NO | new `subscriptions` with full lifecycle |
| entitlements | NO (modules/flags are raw toggles) | new `plan_entitlements` + `subscription_entitlements` mapping onto Phase-6 modules/flags |
| usage metering | NO | new idempotent `usage_events` + `usage_meters` |
| quotas | NO | new atomic `quotas` (included/consumed/reserved/remaining) |
| usage reservation | NO | new reserve→commit/release |
| overage | NO | new governed overage (rate + plan version) |
| billing evidence | NO | new immutable `billing_evidence` snapshots (Phase-2 tax) |
| trials / renewal / suspension / termination | NO | new governed lifecycle |
| marketplace fees + payout | NO | new fee-policy snapshots + payout evidence |
| promotions / discounts | NO | new governed promotions |
| commercial exceptions | NO | new governed, time-bound exceptions |
| entitlement cache | NO | new per-tenant cache (never shared) |

## Summary counts

- **REUSED (no parallel definitions):** Phase-6 modules + feature flags (entitlement backing), Phase-2
  tax (billing), Phase-1 org (provisioning), Phase-9 AI budgets, existing tenants + invoices/payments.
- **NEW governed objects:** products, plan versions (immutable), entitlements, subscriptions, usage
  metering, quotas, reservations, overage, billing evidence, trials/renewal/suspension/termination,
  marketplace fee/payout snapshots, promotions, commercial exceptions, entitlement cache.
- **KEPT DISTINCT:** SaaS subscription fees vs freight/hauling invoices vs carrier payouts.

## Safety commitments

1. **RBAC + entitlement both required** — the entitlement check augments (never replaces) `core.require`.
2. Enforcement is **server-side**; denial categories are returned without leaking other tenant data.
3. Quotas are **atomic** (single UPDATE with a guard) → no negative remaining from races.
4. Metering is **idempotent** (per-tenant idempotency key) → duplicate events do not double-count.
5. Published plan/pricing/billing snapshots are **immutable**; existing subscriptions keep their agreed
   pricing until governed renewal/amendment → historical reproducibility.
6. Provisioning is **idempotent + rollback-capable + fail-closed** — a failed provisioning leaves no
   partially active tenant.
7. Migration is **additive** and fabricates no contract/price/subscription/payment; existing modules,
   flags, users, data, and financial records are preserved → **0 financial / 0 operational / 0
   entitlement-loss / 0 tenant-access drift**.
8. AI never autonomously suspends/terminates a customer (customer-health distinguishes deterministic
   metric vs AI-assisted vs human).
