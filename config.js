/* LiftHaul frontend runtime config — PRODUCTION API wiring.
 *
 * This file is committed to the PUBLIC Pages repo on purpose: an API origin is
 * not a secret. It is the single switch that turns the public LiftHaul pages
 * from the honest offline demo into the real, backend-connected application.
 *
 * HOW TO GO LIVE (after the backend is hosted — see docs/go_live/BACKEND_HOSTING_RUNBOOK.md):
 *   1. Host the backend (Render/Railway/Fly) with PostgreSQL. You get an origin,
 *      e.g. https://lifthaul-api.onrender.com
 *   2. Set apiBase below to that origin (NO trailing slash, https only).
 *   3. Commit + push. GitHub Pages redeploys; every page (landing → Register Free
 *      → provider.html, portal.html, console.html) now talks to the live API.
 *
 * Leave apiBase = "" to keep the pages in honest offline-demo mode (no backend calls).
 * localStorage.lifthaul_api_base still works as a per-browser DEV override and, when set,
 * is used only if apiBase here is empty.
 */
window.RGO_CONFIG = { apiBase: "" };
