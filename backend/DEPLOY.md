# RGO OS — Deployment Guide (host-neutral, Docker-first)

The backend is a standard-library Python app that runs on **SQLite** (dev/tests) or
**PostgreSQL** (production). Docker is the primary, portable deployment method;
Render/Fly.io/Railway all consume the same image.

## 1. Required environment variables (never hard-coded)
| Var | Required | Example | Purpose |
|---|---|---|---|
| `APP_ENV` | prod | `production` | enables strict config validation |
| `APP_SECRET` | prod | (random 32+ chars) | app/session secret |
| `DATABASE_URL` | prod | `postgresql://user:pw@host:5432/rgo` | system of record |
| `CORS_ORIGINS` | prod | `https://rgo.example` | comma-separated allowed frontend origins |
| `PORT` | no | `8787` | listen port (host injects) |
| `APP_DEBUG` | no | `false` | keep **false** in production |
| `WISE_API_KEY` | optional | (secret) | only if the real Wise adapter is enabled — server-side only |
| `SMTP_URL` | optional | (secret) | only if real email is enabled |

In `APP_ENV=production` the server **refuses to start** (exit 2) if `APP_SECRET`,
`DATABASE_URL`, or `CORS_ORIGINS` are missing — safe startup failure.

## 2. Database provisioning & migrations
1. Provision a managed PostgreSQL (Render/Fly/Railway/RDS). Copy its `DATABASE_URL`.
2. Add the driver to the image: uncomment `psycopg[binary]` in `requirements.txt`.
3. Migrations run automatically on release/start via `python migrate.py` (also the
   `release:` Procfile line), which applies the schema and stamps `schema_version`.

## 2b. One-command single-node deploy (available NOW — SQLite + volume)
SQLite here is a real transactional, FK-enforcing DB on a **persistent volume** —
legitimate production for a single small operation (5–20 trucks). No PG refactor needed.
```bash
export APP_SECRET=$(openssl rand -hex 24)
export CORS_ORIGINS=https://your-frontend.example
docker compose up --build            # API on :8787, data persists in the rgo_data volume
```
Demo seed (non-production only): `APP_ENV=development python seed.py`.

**PostgreSQL (multi-node/scale)** additionally needs the small, localized refactor tracked in
`pgcompat.py` (lastrowid→RETURNING, executescript split, ON CONFLICT upsert) before the
`db`/`DATABASE_URL` Postgres path is runtime-ready. `pgcompat` ships the verified param +
DDL translation; the RETURNING refactor is the remaining code task.

## 3. Build & run (Docker, managed host)
```bash
docker build -t rgo-os .
docker run -p 8787:8787 \
  -e APP_ENV=production -e APP_SECRET=$(openssl rand -hex 24) \
  -e DATABASE_URL="postgresql://..." -e CORS_ORIGINS="https://rgo.example" \
  rgo-os
```
Health: `GET /health` (liveness) · `GET /ready` (DB reachable + schema_version).

## 4. Frontend wiring
Serve the frontend with a `config.js` that sets the API base (runtime config, not
localStorage):
```html
<script>window.RGO_CONFIG = { apiBase: "https://api.rgo.example" };</script>
```
`localStorage.rgo_api_base` remains a **dev/demo override only**.

## 5. Backups & restore
- **Backup:** managed-PG automated snapshots (enable PITR). For SQLite,
  `security.backup_db(conn, path)` (online backup) on a schedule.
- **Restore:** provision a new PG from snapshot, point `DATABASE_URL` at it, run
  `python migrate.py` to confirm `schema_version`, redeploy.

## 6. Secret rotation
Secrets live only in the host's secret manager (never in code/frontend). Rotate by
updating the env var and redeploying; sessions expire per `SESSION_TTL` and
`WISE_API_KEY` is fetched server-side at call time via `security.SecretManager`.

## 7. Rollback
Images are immutable and tagged; roll back by redeploying the previous tag. DB
migrations are additive/versioned — a rollback keeps the newer schema (backward
compatible) or restores from the pre-deploy snapshot if a breaking change shipped.

## 8. Owner inputs still required to go LIVE
- selected hosting platform (Render / Fly.io / Railway / other Docker host);
- **PostgreSQL connection URL** (`DATABASE_URL`);
- allowed **frontend origin** (`CORS_ORIGINS`);
- an **application secret** (`APP_SECRET`);
- optional: **Wise** business API key (`WISE_API_KEY`) for the real payment adapter;
- optional: **SMTP** creds (`SMTP_URL`) for real email.

Until these are provided and the hosted `/health`, `/ready`, migrations, and a
frontend API call are verified, live deployment is **BLOCKED ON OWNER INFRASTRUCTURE**.
