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
| `console.html` | **Staff / Operator Console** — the single staff hub (dispatch, fleet, finance, users, etc.) | landing/book/track "Staff login" |

## Staff admin tools (reached from inside the Operator Console)

Linked from `console.html` left-nav ("Admin & Commercial" group) — no longer orphaned:

| Page | Purpose |
|---|---|
| `admin_commercial.html` | Provider accreditation fees & commercial configuration |
| `admin_referral.html` | Referral program administration |
| `samantha.html` | Samantha BD (business-development) surface |

## Frontend → backend wiring

- Production API origin: `config.js` → `window.RGO_CONFIG.apiBase` (the one switch that
  turns the public pages from offline demo into the live app — see
  `docs/go_live/BACKEND_HOSTING_RUNBOOK.md`).
- Per-browser dev override: `localStorage.lifthaul_api_base`.
- `provider.html`, `portal.html`, `console.html` resolve `RGO_CONFIG.apiBase` first, then
  the localStorage override.

## Known cleanup / decisions outstanding (devil's-advocate findings)

1. **Duplicate console — `admin-console.html`** ("Enterprise Administration", 143 KB) is a
   second admin surface that shadows the canonical `console.html` ("Operator Console").
   It is orphaned (linked from nowhere) and uses a *different* API key (`lh_api` instead of
   `lifthaul_api_base`). **Recommendation:** delete it (git-recoverable) or fold anything
   unique into `console.html`. Left in place pending owner call — not part of the live IA.
2. **API-config key drift:** most pages use `lifthaul_api_base` / `RGO_CONFIG`; `admin-console.html`
   uses `lh_api`. Standardize on `RGO_CONFIG.apiBase` + `lifthaul_api_base`.
3. **Legacy `RGO_*` identifiers** remain as internal JS variable names (`RGO_API`,
   `RGO_CONFIG`) — functional, not user-visible; rename opportunistically, not urgently.
4. **Removed:** `rgo-logo.png` (480 KB, orphaned, old brand) — deleted 2026-08-23.

## Naming convention (going forward)

Root pages are flat (GitHub Pages serves them at flat URLs — do **not** move them into
folders without rewriting every link). New admin/staff pages: `admin_<area>.html`
(underscore). Reach every new page from an existing page — never ship an orphan.
