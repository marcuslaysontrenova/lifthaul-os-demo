# LiftHaul isolated recovery validation — approval and evidence boundary

Linked parent: #35, #20. This is a **development-only handoff**, not a production recovery sign-off.

## Existing evidence
- Prior Phase 10 and marketplace CI workflows use ephemeral PostgreSQL 16 GitHub service containers and validate `pg_dump` plus restore to a new database; their successful current runs have **not** been reverified here.
- Current exact-production preflight PR #33 remains separate, at `924b449d3c4ea08a442555b90be67753ec4a431c`, with development CI SUCCESS in run 38606513529.
- PR #33's recovery playbook prints non-executing instructions and has credential redaction tests. **No production backup/restore was performed.**

## Required next isolated CI validation
- Use GitHub-hosted disposable PostgreSQL with fixed localhost identity and only synthetic test records.
- Ensure source and restore target database identities differ; fail before any operation if isolation is not independently proven.
- Do not access Railway production data or credentials. Do not publish SQL dumps as artifacts.
- Compare schema version, governed table row counts and synthetic sentinel integrity; store sanitized pass/fail and timing evidence only.
- Treat local recovery timing as synthetic CI timing, **not** as hosted RTO/RPO.
- Run only within a reviewable development workflow. Do not claim completion until the job itself passes.

## Human-only hosted gate
Named operator to approve and execute backup/restore against a separately authorized isolated target, provide encrypted backup/checksum proof, reconcile permitted integrity evidence, record RTO/RPO and rollback sign-off; no destructive operation on production.

No production changes, payments, dispatch or client-data transfer authorized by this document.
