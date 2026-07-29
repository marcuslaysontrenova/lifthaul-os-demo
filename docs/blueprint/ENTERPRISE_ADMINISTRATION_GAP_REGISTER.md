# Enterprise Administration — Gap Register

> Evidence-based classification of every capability, from **actual repository
> inspection** (schema, migrations, services, routes, UI, tests — not filenames or test
> counts). Drives the gap-only implementation program. Status vocabulary:
> **VERIFIED INSTALLED · PARTIALLY INSTALLED · PRESENT BUT NOT WIRED · MISSING ·
> BLOCKED · LEGACY/DUPLICATED.** Version 0.1 · 2026-07-29.

## Inspection method
`grep` scan for TODO/FIXME/stub/placeholder/mock, in-memory state, tenantless queries,
hardcoded business rules, missing audit/authz, destructive deletes; plus reading the
schemas, `server.py` routes, `admin-console.html`, and the test suite (166 tests). Key
raw findings are cited inline as **Evidence**.

## A. Platform-1 capabilities (governance layer)

| ID | Capability | Status | Evidence | Gap | Remediation / Phase |
|---|---|---|---|---|---|
| C-003 | Tenant foundation | **VERIFIED INSTALLED** (backend) | `tenants` table, seed tenant 0, `test_admin_platform` isolation tests | Tenant dimension exists for Platform-1 entities only | — |
| — | Tenant isolation on **operational** records | **MISSING** | grep `tenant_id` in core/ops/admin/catalog = **0** | operational tables tenantless; no server-side tenant guard | **Phase 1** #4–5 |
| C-005 | Data-driven RBAC + enforcement | **VERIFIED INSTALLED** | `admin_platform` roles/perms/grant matrix; `core.can` honors `actor["perms"]`; `server._actor` enriches; 19 tests | — | — |
| C-006 | User lifecycle | **VERIFIED INSTALLED** | status gate in `core.login`/`actor_for`; suspend/lock/offboard; 8 tests | — | — |
| C-007 | Auth policy / MFA / lockout / sessions | **VERIFIED INSTALLED** | config-driven password policy, persistent login_history, TOTP, session admin, `guarded_login` wired to `/login`; 16 tests | — | — |
| C-004 | Organization hierarchy (backend) | **VERIFIED INSTALLED** | `org.py` 8 tables, graph rules, assignments, org-scoped `authorize`, calendars, cost centers; 25 tests | — | — |
| C-004 | Administration **API** | **VERIFIED INSTALLED** | 36 `/admin/*` endpoints in `server._admin_routes`; permission-gated; 8 HTTP tests | — | — |
| C-004 | Administration **UI** | **PARTIALLY INSTALLED** | `admin-console.html` (menu + live tables + Dialog Standard) | no browser E2E; not wired to a deployed backend; some screens read-only | **Phase 1** #8–15 |
| — | Configuration cascade (backend) | **VERIFIED INSTALLED** | `resolve_config_chain` platform→…→user; `platform_config.effective_to`; org-level; 6 cascade tests | — | — |
| — | Configuration **consumers** | **PRESENT BUT NOT WIRED** | `core.CONFIG={approval_amount_threshold:500000, downpayment_default_pct:30, vat_pct:12}` used in `create_quotation`/approval; parallel `admin_platform` keys seeded but **unconsumed** | business logic ignores the cascade | **Phase 2** |
| — | Audit foundations | **PARTIALLY INSTALLED** | `audit_logs(ts,actor,role,action,entity,entity_id,old,new,reason)` + `core.audit`; pervasive | **no `correlation_id`** column | **Phase 1** #1 |
| — | Tenant backfill | **PARTIALLY INSTALLED** (plan only) | `docs/blueprint/TENANT_BACKFILL_MATRIX.md` | analysis/dry-run/execute/remediation-queue **MISSING** | **Phase 1** #2–7 |

## B. Operational platform (preserve — do not rebuild)

| Capability | Status | Evidence |
|---|---|---|
| CRM customer operations, Bookings, Quotations, Payments, Jobs, Dispatch, Fleet, Finance, Customer portal | **VERIFIED INSTALLED** | commercial spine with separation-of-duties; `test_core/ops/catalog/admin/phase2/server`; real HTTP E2E (31 steps) + restart persistence |
| Persistence (SQLite dev / PostgreSQL prod) | **VERIFIED INSTALLED** | `dbconn.py` adapter; `test_pg_portability` proves RETURNING/NO_ID_TABLES/ON-CONFLICT complete |
| Security controls (pbkdf2, tokens, RBAC, config-validated prod start) | **VERIFIED INSTALLED** | `core`, `security`, `admin_platform`; `test_security` |

## C. Administration subsystems (Section-D navigation areas)

| Area | Status | Evidence | Remediation / Phase |
|---|---|---|---|
| Organization (1) | **VERIFIED (backend+API), UI PARTIAL** | `org.py`, `/admin/org/*`, console | Phase 1 UI/E2E |
| People & Access (2) | **VERIFIED (backend+API), UI PARTIAL** | `admin_platform`, `/admin/users|roles|sessions|…`, console | Phase 1 UI/E2E |
| CRM Administration (3) | **MISSING** | only operational `customers`; no categories/credit/pricing/territories/duplicate/portal/custom-fields | **Phase 3** |
| Master Data (4) | **PARTIALLY INSTALLED** | `master_data` table + `notification_templates`; no governed lifecycle/dependency-check/replacement | **Phase 3** |
| Workflows (5) | **MISSING** | booking/job/quotation state machines hardcoded in `core`/`ops`; `Approval Matrix` conceptual only | **Phase 4** |
| Forms & Fields (6) | **MISSING** | no custom-field framework | **Phase 5** |
| Calendars (7) | **VERIFIED (backend+API), UI PARTIAL** | `org` calendars, `/admin/*-calendars`, console | Phase 1 UI |
| Configuration (8) | **BACKEND VERIFIED, consumers NOT WIRED** | cascade done; consumers hardcoded | **Phase 2** |
| Integrations (9) | **PARTIALLY INSTALLED** | `PaymentProvider`/`NotificationSender` + `MockWise`/`MockSender`; no registry/webhooks/health/replay | **Phase 7** (live BLOCKED w/o creds) |
| Reporting (10) | **MISSING** | only `/reports` rollup; no report definitions/scheduling/row-security | **Phase 8** |
| AI Administration (11) | **MISSING** | front-end "AI assistant" is demo; no registry/prompts/thresholds/override | **Phase 9** |
| Governance (12) | **PARTIALLY INSTALLED** | `/admin/audit`, data-integrity, backfill-status; retention/backup-policy screens MISSING | Phase 1/6 |
| Platform Management (13) | **MISSING** | no module catalog/plans/entitlements/licensing/branding | **Phase 10** |

## D. LEGACY / DUPLICATED (do not extend; converge or retire)

| Item | Status | Evidence | Action |
|---|---|---|---|
| `security.LoginLimiter` (in-memory) | **LEGACY/DUPLICATED** | superseded by `admin_platform` persistent `login_locked`; live `/login` uses `guarded_login`, not `login_guarded` | leave for compat; do not extend; retire in a later cleanup |
| `security.validate_password` (PW_MIN=10 constant) | **DUPLICATED** | superseded by config-driven `admin_platform.validate_password` | converge callers on the config-driven validator |
| `security.actor_checked` (session TTL) | **PRESENT BUT NOT WIRED** | TTL check exists but `_actor` uses `core.actor_for` | wire TTL into session governance (Phase 6 session settings) |

## E. Mock / boundary code (intentional — NOT gaps)

`MockScanner`, `MockSender`, `MockWiseProvider` are the documented provider boundary
(no live credentials). Keep; live validation is **BLOCKED** on owner credentials (Phase 7).

## F. Execution order (dependency-gated)

Phase 1 (C-004 operational + UI) → Phase 2 (config consumers) → Phase 3 (CRM admin +
master data) → Phase 4 (workflow) → Phase 5 (forms) → Phase 6 (platform settings) →
Phase 7 (integrations) → Phase 8 (reporting) → Phase 9 (AI) → Phase 10 (SaaS controls).

## G. Definition of Done (per capability)
Data model · migration · service · API · authorization · tenant isolation · org scope ·
audit · configuration · UI · positive tests · negative tests · browser E2E · restart
persistence · PostgreSQL compatibility · documentation · operational usability.
Backend-only = "backend complete", never "overall complete".
