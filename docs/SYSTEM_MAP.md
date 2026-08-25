# LiftHaul — System Map (site information architecture)

Single source of truth for **what each page is, who it's for, and how it's reached**.
Keep this current when adding/removing a page. Last organized: 2026-08-23.

## Public surface (anyone; no login)

| Page | Purpose | Reached from |
|---|---|---|
| `index.html` | Landing page — the front door for shippers & providers | direct / marketing |
| `provider.html` | Provider & fleet registration workspace | landing "Register free" + role cards + footer |
| `book.html` | Shipper booking (request a delivery) | landing "Book a delivery" |
| `track.html` | Shipment / referral tracking | landing "Track", booking confirmations |

**Public flow:** `index.html` → **Register free** → `provider.html` (providers) · **Book a delivery** → `book.html` (shippers).

## Authenticated surface (login required)

| Page | For | Reached from |
|---|---|---|
| `portal.html` | Carrier / fleet owner portal (own fleet, compliance, accreditation, referrals) | landing "Carrier portal"; post-registration redirect |
| `driver.html` | Driver app (assignments, PODs) | landing "Driver app" |
| `console.html` | **Enterprise Operations Console** — CRM, booking, dispatch, fleet, maintenance, finance, users (the frozen "Enterprise Operations" plane) | landing/book/track "Staff login" |
| `admin-console.html` | **Platform Administration Console** — the "Platform Control" plane: master data, workflow builder, form/document templates, configuration resolve, audit correlation, tenant backfill, reporting, SaaS subscriptions/licensing, tenant-health, marketplace-eligibility | `console.html` nav → "Platform Administration" |

> **The two consoles are NOT duplicates.** They are the two distinct control planes in
> the frozen architecture: `console.html` = **Enterprise Operations**; `admin-console.html`
> = **Platform Control / Administration**. An earlier audit mistook the second for a
> duplicate to retire — corrected 2026-08-25. Do not delete it.

## Staff admin tools (reached from inside the Operations Console)

Linked from `console.html` left-nav ("Platform Control & Commercial" group) — no longer orphaned:

| Page | Purpose |
|---|---|
| `admin-console.html` | Platform Administration control plane (see above) |
| `admin_commercial.html` | Provider accreditation fees & commercial configuration |
| `admin_referral.html` | Referral program administration |
| `samantha.html` | Samantha BD (business-development) surface |

## Frontend → backend wiring (normalized 2026-08-25)

- **One canonical config contract**, resolved in this order by every API-calling page:
  `window.RGO_CONFIG.apiBase` (production, from `config.js`) → `localStorage.lifthaul_api_base`
  (dev override) → legacy `localStorage.lh_api` / `localStorage.rgo_api_base` (backward compat).
- **Every** API page now loads `config.js`, so setting `apiBase` there flips the whole site
  (public + staff) from offline-demo to the live hosted API in one edit — see
  `docs/go_live/BACKEND_HOSTING_RUNBOOK.md`.

## Known cleanup / decisions outstanding

1. **Legacy `RGO_*` identifiers** remain as internal JS variable names (`RGO_API`,
   `RGO_CONFIG`) and the internal tenant id (`TENANT.id="RGO"`, the backend tenant key for
   Tenant Zero) — functional, not user-visible; rename opportunistically, not urgently.
2. **Removed:** `rgo-logo.png` (480 KB, orphaned, old brand) — deleted 2026-08-23.
3. **Visible RGO branding:** none remaining in product UI — the last "RGO tenant" labels in
   `console.html` were reworded to "Tenant Zero" (2026-08-25).

## Naming convention (going forward)

Root pages are flat (GitHub Pages serves them at flat URLs — do **not** move them into
folders without rewriting every link). New admin/staff pages: `admin_<area>.html`
(underscore). Reach every new page from an existing page — never ship an orphan.
