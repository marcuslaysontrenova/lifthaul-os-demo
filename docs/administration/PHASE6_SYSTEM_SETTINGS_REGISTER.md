# PHASE 6 — System Settings Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-02
**Scope:** governed Platform & System Settings — settings/secrets/security-policy/numbering/currency/
calendars/branding/templates/retention/file/API/feature-flags/modules/maintenance/backup.
**Method:** full inspection of `server.py`, `security.py`, `db.py`, `admin_platform.py` (C-007 auth),
`config_registry.py` (Phase 2), `org.py` (calendars), `crm_admin.py` (Phase-3 numbering), `masterdata.py`.

> Guiding rules (directive): **security invariants are NOT tenant-disableable** (a tenant may
> strengthen, never weaken below the platform minimum); **secrets are never stored in ordinary
> settings tables** (references only, masked, never logged/exported); reuse the Phase-2 config
> cascade, Phase-3 numbering, the tax-policy model, and the calendar engine — no parallel models.

## Classification legend

`SECURITY INVARIANT` · `PLATFORM SETTING` · `TENANT SETTING` · `ORGANIZATION OVERRIDE` ·
`USER PREFERENCE` · `SECRET` · `FEATURE FLAG` · `MODULE ENTITLEMENT` · `OPERATIONAL DEFAULT` ·
`LEGACY CONSTANT` · `UNUSED` · `EXTERNAL PROVIDER CONFIGURATION`.

---

## A. Environment / bootstrap (retained in environment; NOT moved into settings tables)

| Key | Source | Code | Scope | Secret | Class | Recommendation |
|---|---|---|---|---|---|---|
| `DATABASE_URL` | env | db.py:96 | platform | **yes (connection)** | SECRET / EXTERNAL PROVIDER CONFIG | keep in env; secret-reference metadata only |
| `APP_SECRET` | env | server.py:36 | platform | **yes** | SECURITY INVARIANT / SECRET | keep in env; never in settings/audit/export |
| `APP_ENV` / `APP_DEBUG` / `PORT` | env | server.py:32-34 | platform | no | PLATFORM SETTING (bootstrap) | keep in env (restart-required) |
| `CORS_ORIGINS` | env | server.py:35 | platform | no | SECURITY INVARIANT | keep in env; mirror as read-only API-policy view |
| `WISE_API_KEY` | env | security.py:123 | platform | **yes** | SECRET / EXTERNAL PROVIDER CONFIG | secret reference (Phase 7 integration); never displayed |
| `FIREBASE_KEY`-style | env | (deploy) | platform | **yes** | SECRET | env only |

## B. Security / authentication / session (C-007 — platform-minimum enforced)

| Key | Source | Class | Platform min? | Recommendation |
|---|---|---|---|---|
| `auth.password.min_length` | admin_platform C-007 config | SECURITY INVARIANT | yes | platform floor; tenant may raise, not lower |
| `auth.password.complexity` / `history` / `expiry_days` | C-007 config | SECURITY INVARIANT | yes | governed; tenant-strengthen only |
| `auth.lockout.threshold` / `duration` / `window` | C-007 config | SECURITY INVARIANT | yes | platform floor |
| `auth.mfa.policy` (off/optional/required) | C-007 config | SECURITY INVARIANT | yes | tenant may require; not weaken below platform |
| `session.duration` / `idle_timeout` / `absolute_timeout` | C-007 config | SECURITY INVARIANT | yes | platform max ceiling (tenant may shorten) |
| `session.concurrent_limit` / `revocation` | sessions table | SECURITY INVARIANT | yes | governed |

## C. Phase-2/3/5 governed models (REFERENCED, not duplicated)

| Concern | Canonical source | Phase-6 action |
|---|---|---|
| tax rate/mode/type/withholding | `config_registry` + `policy.evaluate_tax` (Phase 2) | reference in Currency & Fiscal settings; **do not duplicate** |
| approval / downpayment thresholds | Phase-2 policy | reference; unchanged |
| customer numbering | `crm_admin` (Phase 3) | generalize numbering admin to more entities; reuse the sequence engine |
| working / holiday calendars, business hours | `org` engine (Phase 1 C-004) | reuse for Business Hours; **no parallel calendar** |
| form field sensitivity / masking | `forms` (Phase 5) | reuse masking discipline for secrets |

## D. Hardcoded operational defaults → governed platform/tenant settings (new)

| Setting | Current | Class | Recommendation |
|---|---|---|---|
| platform name / locale / timezone / currency | implicit | PLATFORM SETTING | governed definitions (safe display) |
| supported countries / currencies | implicit | PLATFORM SETTING | governed lists (reuse master data) |
| quotation validity days | `config_registry` (Phase 2) | OPERATIONAL DEFAULT | reference; expose in tenant settings |
| fiscal year start / periods | absent | TENANT SETTING | new governed settings |
| file limits / allowed types | `forms.upload_file` params (Phase 5) | PLATFORM/TENANT (min) | governed File Policy (tenant stricter only) |
| API rate/burst/timeout/retry | absent | PLATFORM/TENANT | governed API Policy (tenant not weaker) |
| retention periods (audit/session/docs/…) | absent | PLATFORM/TENANT | governed Retention (audit = platform min) |
| branding (logo/colors/header/footer) | absent | TENANT SETTING | governed, sanitized branding |
| document/notification templates | `wfgov.notification_events` queue (Phase 4) | TENANT SETTING | governed templates, allowlisted variables |

## E. New governance objects (Phase 6)

| Object | Class | Notes |
|---|---|---|
| feature flags | FEATURE FLAG | platform default + tenant override + scheduled + kill-switch + dependency; never bypasses security |
| modules | MODULE ENTITLEMENT | dependency + unsafe-disable guard + impact preview |
| maintenance mode | PLATFORM/TENANT | scoped, expiry-bound, bypass permission; platform-wide gated to platform admin |
| backup policy + runs | PLATFORM SETTING | metadata + checksum; **no raw cloud creds** (secret reference); governed restore approval |
| secret references | SECRET | metadata only (provider/scope/rotation/last-verified); value never stored |

## F. Secret boundary (enforced)

Secrets are stored as **references** (`secret_references` table): provider, scope, owner, rotation/
verified metadata, masked hint — **never the value**. The value lives in the environment / external
store (`security.SecretManager`). After creation the value is never displayed, logged, audited, or
exported. Ordinary `setting_definitions` marked `secret=1` reject value writes (as in Phase 2).

## Summary counts

- **SECURITY INVARIANTS (platform-minimum, tenant-strengthen only):** ~12 (password/lockout/MFA/session/CORS).
- **PLATFORM / TENANT SETTINGS (new governed):** ~30 (identity, fiscal, file, API, retention, branding).
- **REFERENCED existing models (not duplicated):** tax, approval, downpayment, numbering, calendars.
- **SECRETS (references only):** DATABASE_URL, APP_SECRET, WISE_API_KEY, provider creds.
- **NEW governance objects:** feature flags, modules, maintenance, backup/restore, secret references.

## Safety commitments

1. Security invariants are enforced as **platform minimums**; a tenant setting below the platform
   floor is rejected → **UNEXPECTED SECURITY POLICY WEAKENING = 0**.
2. Settings changes never recompute a financial value (tax stays in the Phase-2 model) →
   **UNEXPECTED FINANCIAL DIFFERENCES = 0**.
3. Settings changes never mutate operational transaction status →
   **UNEXPECTED OPERATIONAL STATUS CHANGES = 0**.
4. Secrets are references only; values are never stored, displayed, logged, audited, or exported.
5. Setting/template versions are immutable where versioned; historical documents are never
   retroactively changed.
