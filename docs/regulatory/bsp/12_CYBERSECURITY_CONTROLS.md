# 12 — Cybersecurity Controls

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

## Access & identity

- **Backend is the RBAC authority** — the UI holds no JS permission engine; permissions come from
  `/me/permissions`. Enforced by `core.require`/`can`.
- Separation of duties on payments (maker ≠ checker), disputes (opener ≠ resolver), assignments
  (assigner ≠ approver).
- Account lockout on repeated failures (`guarded_login`), configurable via the auth policy cascade.
- MFA policy configurable (`auth.mfa_policy`); MFA required for payout approval.

## Data protection

- **Payment secrets never in business tables** — provider credentials in the integration-secret store.
- Immutable audit ledger (who/what/when/correlation-id) for every state change.
- Webhook verification: signature + replay + idempotency before acting on provider callbacks.

## Application security

- Parameterized queries throughout; PostgreSQL-portability guard in tests.
- Tenant isolation: 404-no-leak `tenant.guard`; cross-tenant reads blocked (`tenant.predicate`).
- Fail-closed provider adapter (unsupported capability → error, never silent success).

## Attack-surface review

`docs/PROTECTED_TRANSACTION_ATTACK_MATRIX.md` (red-team matrix) enumerates abuse cases and the
enforcing control for each. Regression suite exercises the negative paths.

## To be completed with the owner / provider

- Production secret management + rotation policy (host-level).
- Penetration test by an independent party before live funds.
- TLS/network controls at the production host.
