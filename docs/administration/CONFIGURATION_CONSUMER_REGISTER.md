# Configuration Consumer Register (Phase 2)

> Evidence-based inventory of every configuration source/consumer, from repository
> inspection (`grep core.CONFIG`, env vars, hardcoded constants). Drives the Phase-2
> conversion of appropriate hardcoded rules into governed, typed, tenant/org-aware,
> effective-dated configuration — **without changing any existing financial value**.
> Version 0.1 · 2026-08-02.

## Classification legend
SECURITY INVARIANT · PLATFORM POLICY · TENANT BUSINESS POLICY · ORGANIZATION POLICY ·
USER PREFERENCE · OPERATIONAL DEFAULT · LEGACY CONSTANT · UNUSED · SECRET · FEATURE FLAG.

## A. Financial / business-rule consumers (Phase-2 conversion targets)

| Key (canonical) | Current source | Code location | Category | Type | Default | Scopes | Snapshot req'd | Action |
|---|---|---|---|---|---|---|---|---|
| `quotation.approval.threshold_amount` | `core.CONFIG["approval_amount_threshold"]` | `core._needs_approval` (submit) | TENANT BUSINESS POLICY | currency | 500000 | platform→tenant→BU→branch | yes (approval) | **CONVERT** |
| `quotation.approval.discount_threshold_pct` | `core.CONFIG["approval_discount_pct"]` | `core._needs_approval` | TENANT BUSINESS POLICY | percent | 10 | platform→tenant→BU→branch | yes | **CONVERT** |
| `tax.default.rate` | `core.CONFIG["vat_pct"]` | `core.create_quotation` | TENANT BUSINESS POLICY | percent | 12 | platform→tenant→branch | yes (tax) | **CONVERT** |
| `tax.default.code` | (implicit "VAT") | `core.create_quotation` | TENANT BUSINESS POLICY | string | VAT | platform→tenant→branch | yes | **CONVERT** |
| `tax.rounding_mode` | `round()` (implicit) | `core.create_quotation` | PLATFORM POLICY | enum | round | platform→tenant | yes | **CONVERT** |
| `payment.downpayment.default_rate` | `core.CONFIG["downpayment_default_pct"]` | `core.create_quotation` | TENANT BUSINESS POLICY | percent | 30 | platform→tenant→BU→branch | yes (downpayment) | **CONVERT** |
| `payment.downpayment.minimum_rate` | (none) | — | TENANT BUSINESS POLICY | percent | 0 | platform→tenant→BU→branch | yes | **CONVERT (new floor)** |
| `payment.downpayment.required` | (implicit true) | `core.create_payment_request` | TENANT BUSINESS POLICY | boolean | true | platform→tenant→BU→branch | yes | **CONVERT** |
| `quotation.validity_days` | `admin_platform.DEFAULT_CONFIG` | (display) | OPERATIONAL DEFAULT | integer | 30 | platform→tenant | no | governed (exists) |
| `separation_of_duties` | `core.CONFIG["separation_of_duties"]` | `core.approve_quotation` | **SECURITY INVARIANT** | boolean | true | platform only | n/a | **RETAIN (not tenant-disableable)** |

## B. Numbering (retain as platform for now — deterministic, low value to expose)

| Concern | Current | Category | Action |
|---|---|---|---|
| quotation numbering `QN-{3001+n}` | `core.create_quotation` | OPERATIONAL DEFAULT | retain (candidate `numbering.quotation.pattern` later) |
| booking `BK-{1000+n}` / job `JO-{2050+n}` / invoice `INV-{9001+n}` / PR `PR-{5001+n}` | core/ops | OPERATIONAL DEFAULT | retain (later phase) |

## C. Already-governed (Phase 1 cascade) — no conversion needed

`iam.rbac_source`, `auth.pw_min_length`, `auth.pw_require_complexity`, `auth.lockout_threshold`,
`auth.lockout_window_min`, `auth.mfa_policy`, `dispatch.double_book` — resolved via the verified
cascade. `auth.*` and `iam.*` are SECURITY-sensitive PLATFORM/TENANT policy (not user-disableable).

## D. Secrets (never in ordinary config)

`WISE_API_KEY`, `APP_SECRET`, `SMTP_URL`, `DATABASE_URL` — SECRET, via `security.SecretManager` /
env only. **Do not** move into `platform_config`.

## E. Feature flags / operational

Docker/`APP_ENV`/`PORT`/`CORS_ORIGINS` — OPERATIONAL/DEPLOYMENT (env). `dispatch.double_book` —
FEATURE/POLICY (governed).

## Conversion summary
- **Convert (Phase 2):** the 8 financial/business keys in §A (approval threshold + discount,
  tax rate/code/rounding, downpayment rate/min/required).
- **Retain:** `separation_of_duties` (security invariant), numbering (later), secrets, deployment env.
- **Guardrail:** every converted default is seeded to **exactly** today's constant, and the existing
  financial regression (subtotal 600000 → total 672000 → dp 201600) is the proof of unchanged totals.
