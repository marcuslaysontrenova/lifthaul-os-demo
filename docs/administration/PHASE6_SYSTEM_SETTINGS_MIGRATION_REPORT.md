# PHASE 6 — System Settings Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** introduce governed Platform & System Settings WITHOUT weakening any security policy,
WITHOUT changing any financial value or operational status, and WITHOUT exposing any secret.

## Migration strategy (additive, non-destructive, secret-safe)

Phase 6 adds a governed settings layer (`setting_definitions` / `setting_values`), a secret-reference
boundary, feature flags, a module registry, maintenance mode, retention policies, backup/restore
governance, branding, and templates. It **references** the Phase-2 tax/approval/downpayment policy,
Phase-3 numbering, and the Phase-1 calendar engine rather than duplicating them. Bootstrap/security
env vars (`DATABASE_URL`, `APP_SECRET`, `APP_ENV`, `PORT`, `CORS_ORIGINS`, `WISE_API_KEY`) **remain in
the environment**; they are represented only as secret references (metadata), never copied into tables.

| Existing surface | Migration action | Impact |
|---|---|---|
| env vars (secrets + bootstrap) | retained in env; secret-reference metadata only | none — values never stored |
| C-007 auth/session policy (admin_platform config) | represented as security-invariant definitions with platform floors | none — same or stronger |
| Phase-2 tax/approval/downpayment | referenced (Currency & Fiscal view) | none — not duplicated |
| Phase-3 numbering | referenced/generalized | none |
| Phase-1 calendars | reused for Business Hours | none |
| new governed objects (flags/modules/maintenance/retention/backup/branding/templates) | additive tables | additive only |

## Classification

| Class | Handling |
|---|---|
| deterministic | mapped to typed definitions with defaults == current constants |
| safe default | definition default_value == existing behavior |
| secret reference | value stays in env/store; metadata only (provider/scope/rotation/verified) |
| platform minimum | security invariants (password/lockout/MFA/session/audit-retention) enforced as floors |
| tenant override | allowed only where the definition permits and never below a security floor |
| organization override | branch/BU/dept/team/user where the definition permits |
| deprecated | none |
| excluded | raw credentials (never stored), historical financial documents (never changed) |

## Migration results

| Metric | Result |
|---|---|
| Settings discovered | env vars + C-007 policy + operational defaults |
| Settings migrated to definitions | 20 governed definitions seeded |
| Settings retained in environment | `DATABASE_URL`, `APP_SECRET`, `APP_ENV`, `PORT`, `CORS_ORIGINS`, `WISE_API_KEY` |
| Secret references created | as needed (metadata only) |
| Ambiguous settings | 0 |
| Invalid settings | 0 (typed validation) |
| **Financial differences** | **0** |
| **Operational status differences** | **0** |
| **Security-policy weakening** | **0** |

## Invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED OPERATIONAL STATUS CHANGES = 0
UNEXPECTED SECURITY POLICY WEAKENING = 0
```

- **Security:** a tenant/org value below the platform floor is rejected (`ForbiddenError`); the
  integrity check `tenant_policy_below_platform_minimum` reports 0. Tenants may strengthen only.
- **Financial:** settings changes never recompute a financial value — tax stays in the Phase-2 model;
  `test_settings_do_not_change_financials` asserts quotation `tax`/`total` unchanged (72000/672000).
- **Operational:** settings changes never mutate transaction status.
- **Secrets:** values are never stored, displayed, logged, audited, or exported — only references +
  masked hints; `validate_secret_reference` returns a boolean presence flag only.

## Reversibility

- Only additive DDL (`CREATE TABLE IF NOT EXISTS`); no column drops; historical documents untouched.
- Setting values are versioned (supersede, never destroy); prior versions remain queryable.
- Feature flags, modules, maintenance windows, and templates can be disabled/retired without deleting
  audit history.
