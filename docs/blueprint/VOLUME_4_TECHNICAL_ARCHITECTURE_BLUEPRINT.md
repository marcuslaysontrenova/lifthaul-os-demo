# Volume 4 — Technical Architecture Blueprint

> Services, APIs, database domains, integration patterns, deployment topology,
> security, observability, and scalability. This volume is the engineering contract
> that realizes Volumes 1–3 on top of — and without discarding — the current stack.
> Version 0.1 (DRAFT).

---

## 1. Architectural stance

- **Modular monolith first, service-extractable later.** The current backend is a
  cohesive Python app with clean module seams (`core`, `ops`, `admin`, `catalog`,
  `security`, `dbconn`). We keep the monolith deployable unit and enforce **domain
  boundaries in-process** (one module owns each platform's tables). Extraction to
  services happens only when a boundary proves it needs independent scale.
- **Boundaries over layers.** Code is organized by platform/domain, not by technical
  layer. Cross-domain calls go through published module interfaces, never foreign SQL.
- **Everything tenant-scoped.** A data-access guard injects `tenant_id` on every query;
  a query without a tenant predicate is a bug the test suite must catch.

## 2. Domain decomposition (maps to the 9 platforms)

| Domain module | Platform | Current file(s) | Target responsibility |
|---|---|---|---|
| `identity` | 1 | `security.py`, part of `core.py` | tenants, users, roles, permissions, sessions, MFA, password policy, login history |
| `orgstructure` | 1 | — (new) | branches, departments, business units, cost centers, holidays |
| `config` | 1 | `catalog.py` (`system_config`), `admin.py` (`master_data`) | limits registry, master data, config cascade resolver |
| `workflow` | 1/3 | — (new; logic today embedded in `core`/`ops`) | workflow definitions + engine, approval matrix, SLA, escalation |
| `commercial` | 2 | `core.py` (customers→invoices), `catalog.py` (contacts/addresses) | CRM, bookings, quotations, contracts, pricing, credit, invoices, portal |
| `operations` | 3 | `ops.py`, `catalog.py` (equipment/vehicles/employees/maintenance), `admin.py` (safety/incidents) | jobs, dispatch, fleet, scheduling, maintenance, safety, crew, GPS |
| `finance` | 4 | `ops.py` (expenses/change_orders/invoices), `admin.py` (suppliers/PO), `catalog.py` (supplier_invoices) | GL, tax, billing, collections, profitability, forecasting, budget |
| `analytics` | 5 | `/reports` in `server.py` | KPIs, dashboards, warehouse, reporting |
| `ai` | 6 | — (new) | model registry, prompt library, confidence, override, learning logs |
| `integration` | 7 | `PaymentProvider`, notification sender | gateway, webhooks, connectors, Open API, SDK |
| `platform` (devops) | 9 | `db.py`, `migrate.py`, `server.py` config/health | flags, monitoring, backups, secrets, environments |

## 3. Data architecture

- **Store:** PostgreSQL in production (system of record), SQLite for dev/test. The
  `dbconn.py` adapter makes service code portable and is **statically proven complete**
  (`test_pg_portability.py`; see `backend/PG_PORTABILITY_AUDIT.md`).
- **Tenant column:** add `tenant_id` (FK → `tenants`) to every domain table; composite
  indexes lead with `tenant_id`. Backfill RGO rows to tenant 0 in a migration.
- **Schema management:** `schema_version` + `migrate.py`; migrations are additive and
  versioned (rollback keeps forward-compatible schema). Every new table ships with its
  migration and is registered in `NO_ID_TABLES` correctly if id-less (guard test).
- **Audit store:** `audit_logs` (append-only) is the cross-domain event spine; every
  mutating command writes one. `login_history` mirrors it for authn.
- **Config store:** `(scope, scope_ref, key, value, updated_by, updated_at)` powers the
  cascade resolver (Volume 2 §4). `system_config` is generalized into this shape.

## 4. Service & API design

- **Transport:** HTTP/JSON. Today a stdlib `ThreadingHTTPServer`; the framework is an
  implementation detail behind the routing table (`_routes`/`_ops_routes`/
  `_phase2_routes`). Concurrency currently serialized by `_DB_LOCK` + connection with
  `check_same_thread=False` (see prior threading fix) — acceptable for the monolith;
  revisit with a connection pool when moving to Postgres under load.
- **API conventions:** resource-oriented paths; verbs for domain actions
  (`/quotations/:id/approve`); envelope `{ "data": … }` / `{ "error": … }`;
  idempotency keys on finance actions; every request carries tenant + actor context.
- **Command pattern:** each mutating endpoint = permission check → validation →
  maker/checker (if required) → domain mutation → `audit_logs` event → optional
  workflow transition → response. This is already the shape of the commercial spine;
  generalize it.
- **Open API (P7):** publish an OpenAPI spec generated from the routing table; the SDK
  and external integrations consume it.

## 5. Workflow engine (realizes G-04)

- **Definition:** a workflow is `states[]`, `transitions[] (from,to,guard,effects)`,
  and `required_fields` per state — stored as versioned config, never edited in place.
- **Execution:** a small engine evaluates guards (permission, amount band, resource
  availability) and applies effects (status change, audit event, notification). The
  current booking/job stage machine and quotation approval become the **seed
  definitions**, proving parity before any behavior changes.
- **Approval Matrix** and **SLA/Escalation** are workflow guards/timers reading Org
  Holidays and Finance Rules. This removes hard-coded thresholds (₱500k, 30%, 12%).

## 6. Integration architecture (Platform 7)

- **API Gateway:** authentication (API keys/OAuth), rate limiting, tenant resolution,
  request logging — a front door for all external traffic.
- **Webhooks:** tenant-configurable subscriptions to domain events (from `audit_logs`),
  signed payloads, retry with backoff, delivery log.
- **Connectors:** adapter pattern (the existing `PaymentProvider`/`MockWiseProvider` is
  the template) for Wise, QuickBooks, SAP, Oracle, Google/Microsoft (SSO + calendar).
  Credentials live in the encrypted vault (§7), never in code or the browser.
- **SDK:** thin client over the Open API spec.

## 7. Security architecture

- **AuthN:** pbkdf2 password hashing (present), token sessions (present), + MFA/TOTP
  and password policy (G-11), + SSO (OAuth/SAML) via Platform 7.
- **AuthZ:** data-driven RBAC (G-02) enforced server-side on every command; tenant
  isolation enforced in the data-access guard; separation of duties preserved.
- **Secrets:** `security.SecretManager` interface exists; back it with the host secret
  manager / per-tenant encrypted vault. No secret in code, logs, or the frontend
  bundle (already asserted by tests — extend to a CI secret-scan).
- **Data protection:** encryption in transit (TLS at the host), at rest (managed PG +
  vault); PII minimization; audit retention policy per tenant.
- **Startup safety:** production refuses to boot without `APP_SECRET`/`DATABASE_URL`/
  `CORS_ORIGINS` (present); extend validation to required tenant/security config.

## 8. Observability

- **Logging:** structured JSON with request-id + duration (present in `_send`); add
  tenant-id + actor + capability to every log line.
- **Metrics:** per-capability latency/error counts; queue/drain style counters for any
  async work; SLA breach counters. Surface to the monitoring stack (G-10).
- **Tracing:** request-id propagated across domain calls; ready for distributed tracing
  when/if services are extracted.
- **Health:** `/health` (liveness) + `/ready` (DB + schema_version) present; add
  dependency checks (vault, connectors) to `/ready`.

## 9. Deployment topology

- **Packaging:** Docker; one image runs on SQLite (dev) or PostgreSQL (prod). Compose
  stack (nginx frontend + backend + postgres:16 volume) is the local full-stack.
- **Environments (G-10):** dev → staging → prod, each with its own DB, secrets, and
  config; environment banner in the UI (System Config).
- **Releases:** immutable tagged images; migrations run on release; rollback = redeploy
  previous tag (schema is forward-compatible) or restore snapshot for breaking changes.
- **The open gate:** the **live browser→API→PostgreSQL→restart** proof
  (`DEPLOYMENT_VALIDATION.md`, 15 checks) is **not yet executed** (no Docker/PG host in
  the build env). It remains the release gate; static PG portability is proven but is
  not a substitute (Volume 1 G-14).

## 10. Scalability

- **Vertical first**, then horizontal read replicas for analytics; the modular monolith
  scales a long way on managed PG.
- **Tenant sharding** (schema-per-tenant or DB-per-large-tenant) is the escape hatch for
  outsized accounts; row-level scoping is the default and keeps the option open.
- **Async work** (notifications, webhooks, AI, heavy reports) moves to a queue when the
  synchronous path pressures request latency; today's synchronous drain patterns are the
  seam.
- **Connection pooling** replaces the single locked connection when Postgres concurrency
  is exercised under real load.

## 11. Engineering invariants (CI-enforceable)

1. No query without a `tenant_id` predicate (lint/test).
2. No mutating endpoint without an `audit_logs` write (test).
3. No business limit/state as a code constant where Volume 2 says it is config.
4. `NO_ID_TABLES`/portability guard stays green; every new table has a migration.
5. No secret in code/logs/bundle (CI secret-scan).
6. Every capability ships with unit + (where cross-cutting) HTTP E2E coverage.
