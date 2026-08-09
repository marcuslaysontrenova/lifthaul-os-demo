# 05 — Payment-System Architecture

```
BSP OPS STATUS: REGULATORY CLASSIFICATION / APPLICATION PREPARATION
```

## Components

| Layer | Module | Role |
|---|---|---|
| Orchestration domain | `backend/protected_payment.py` | Canonical state machine, ledger, reconciliation, provider interface, certification harness |
| Payment requirements/config | `backend/marketplace_payments.py` | Payment requirement on assignment; `live_funds_enabled` gate |
| Trust/eligibility | `backend/marketplace_trust.py`, `marketplace_trust_closure.py` | KYB, fraud, driver/vehicle legality, payout security, dispute lifecycle, release gate, risk limits, webhook verify, reconcile ledger |
| Carrier authority | `backend/ltfrb.py` | LTFRB CPC records, verification, hard assignment gate |
| API | `backend/server.py` | Admin/finance routes (`/admin/marketplace/protected-payments*`, `/admin/marketplace/ltfrb/*`, `/admin/marketplace/regulatory-summary`) |
| UI | `index.html` | Finance Protected Payment control center, customer/carrier projections, Regulatory Compliance dashboard |
| Provider | external | BSP-regulated; implements `ProtectedPaymentProvider` |

## Trust boundaries

- **No payment secrets in business tables.** Provider credentials/secrets live only in the
  integration-secret store (`integrations`/`settings` secret handling), never in `mkt_*` tables.
- **Provider adapter is fail-closed** — unsupported capability → `PROVIDER_CAPABILITY_NOT_SUPPORTED`.
- **Webhook verification** (signature + replay + idempotency) before any state change from a provider
  callback.

## Data at rest

- Transaction refs, amounts, state, evidence references, ledger entries — in the platform DB
  (SQLite dev / PostgreSQL prod).
- Immutable ledger: append-only; corrections are reversing entries, never mutations.

## Environments

- Dev: SQLite in-memory / file.
- Prod: PostgreSQL (`DATABASE_URL`), containerized (`Dockerfile`, `docker-compose.yml`).
- Live funds path disabled until the three-flag gate is satisfied.
