# Browser E2E Evidence — LiftHaul Enterprise RC1

Real browser automation against a **live running backend** (not service tests, not a static
snapshot). Executed by serving the SPA over HTTP and driving it in an automated browser:

```
backend:   python backend/server.py           (PORT=8830, CORS_ORIGINS=*, SQLite dev DB)
frontend:  python -m http.server 8821          (serves index.html over real HTTP → JS executes)
browser:   automated tab @ http://localhost:8821/index.html, localStorage.rgo_api_base=backend
```

The earlier "static snapshot" limitation was a `file://` artifact; serving over HTTP makes the
application fully live in the browser.

## Verified live (browser DOM + live cross-origin API)

| Persona / flow | What was driven | Result |
|---|---|---|
| Live app boot | JS runs; `RGO_API` present; browser `fetch(/health)` cross-origin | JS ✓, CORS ✓, health 200 |
| Admin login | real login form → live `/login` → `/me/permissions` | role=admin, 441 permissions, financial flags all true |
| **Finance Administrator** | login via UI form → nav gating from `/me/permissions` | role badge `finance_admin`; **Bookings hidden**; Quotations + Finance + Invoices visible |
| **Admin → Trust & Compliance** | UI nav to People & Access → Trust tab (live fetch) | KYB, Payout approvals, Disputes, Claims panels render; integrity "✓ no fabricated verifications" |
| Pricing subsystem | `price-preview` with a tampered `subtotal:999999` | server-authoritative ₱174,000 (tampered ignored), margin 32.76% |
| Rate catalog | `/rate-catalog` | 8 items |
| Marketplace trust gate | eligibility for an unverified carrier | eligible=false — "carrier business not verified (KYB=NONE)" |

## Status

- **Browser E2E of the application: PASS** (live backend, real browser, real UI navigation,
  backend-authoritative gating, pricing recompute, trust gate — all confirmed).
- **PostgreSQL-backed** browser E2E specifically: the same flows against PostgreSQL still require a
  running PostgreSQL server. This host has psycopg2 installed and the code is PostgreSQL-portable
  (`test_pg_portability.py` green), but no PG server / Docker is available and a system-wide install
  needs admin — an owner/infrastructure step. Point `DATABASE_URL` at any PostgreSQL and re-run the
  identical flows to close this variant.
