# RGO OS — Deployment Validation Gate

**Current gate: READY FOR DEPLOYMENT VALIDATION** (not "ready with conditions",
not "production ready"). The code is implementation-complete and deployment-
prepared and passes 89 automated unit/API tests, but the integrated runtime proof
below has **NOT** been executed in the build environment (no Docker/PostgreSQL
runtime available here). Do not claim production readiness until this passes.

## The proof that is still missing
```
Browser → Frontend → Backend → PostgreSQL → Persistent Data → Restart Verification
```

## Run on any Docker-capable host
```bash
docker compose up --build
```

## Acceptance checklist (tick only after observing it pass)
- [ ] All containers become healthy (`db`, `backend`, `frontend`)
- [ ] Migrations execute successfully (`schema_version` stamped)
- [ ] Frontend loads (http://localhost:8080)
- [ ] Frontend connects to the backend (API calls succeed)
- [ ] Backend connects to PostgreSQL
- [ ] Authentication and role permissions work end-to-end
- [ ] Full business lifecycle completes (booking → quotation → PDF → payment →
      confirmed job → dispatch → change order → completion → final invoice → close)
- [ ] Data remains after browser refresh
- [ ] Data remains after backend restart
- [ ] Data remains after container restart
- [ ] Backup succeeds (`make backup`)
- [ ] Restore succeeds (`sh scripts/restore.sh < backup.sql`)
- [ ] Logs contain request/correlation IDs
- [ ] No secrets appear in the browser bundle or logs
- [ ] Failure paths behave (unauthorized, duplicate, conflict, block, isolation)

## Status transitions
- **After all boxes pass:** reclassify to `READY WITH CONDITIONS` — remaining
  conditions limited to owner-controlled infrastructure: hosting, TLS, domain,
  managed PostgreSQL, production secrets, restricted CORS, backups, monitoring,
  optional Wise/email.
- **Only after** the *hosted* app + DB connection + migrations + health checks +
  frontend API integration + persistent workflow are verified may live deployment
  be claimed.

## Final CTO decision (current)
```
WHOLE-SYSTEM PRODUCTION READINESS: NOT READY
CURRENT GATE:                      READY FOR DEPLOYMENT VALIDATION
NEXT ACTION:                       Run the complete Docker Compose browser-to-PostgreSQL E2E on a Docker-capable host.
DECISION AFTER PASS:               Upgrade to READY WITH CONDITIONS and proceed to controlled cloud deployment.
```
