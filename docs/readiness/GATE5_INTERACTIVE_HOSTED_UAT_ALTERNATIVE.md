# Gate 5 alternate hosted UAT — no GitHub credential secret required

**Status: alternative operator-controlled path; not Gate 5 PASS.**

Codex's `scripts/go_live/prod_persona_uat.py` is the acceptance authority. The existing GitHub workflow needs protected `LH_PROD_PERSONA_CREDENTIALS_JSON`. An alternate runner, `scripts/go_live/interactive_persona_uat.py`, accepts the SAME 11 approved existing synthetic accounts through private, no-echo terminal prompts and uses the SAME hosted production API checks, including exact-release SHA, RBAC and owned marker checks, 10 revoked sessions, no live funds and cross-persona isolation. It writes a redacted local artifact; an independent QA reviewer must still approve it. **It does not create users, seed production, authorize deployment, bypass approval, or move funds.**

## Operator procedure
1. Verify that all eleven unique test identities and owned synthetic fixture markers actually exist and are expressly authorized for a no-live-money production UAT. A named security administrator provisions them through governed existing admin and onboarding paths if necessary. Do not use `backend/seed.py` (it is intentionally production-disabled).
2. Obtain independent review of this PR and PR #58, then separately approve any merge into `main`, which may automatically deploy production.
3. On the approved operator computer, retrieve the exact released source and confirm it is the deployed 40-character SHA. Ensure access to the canonical domain. Use a private terminal (not screenshots, CI or shared logs).
4. Run `python scripts/go_live/interactive_persona_uat.py` alone first; safe mode makes **no requests**.
5. After authorization, run `python scripts/go_live/interactive_persona_uat.py --execute --expected-sha FULL_DEPLOYED_40CHAR_SHA --approval-reference APPROVED_CHANGE_TICKET`. The tool checks `/readyz` and rejects wrong deployment BEFORE requesting passwords. Enter each approved login/password/fixture marker privately. Never supply via command-line args or ChatGPT.
6. Securely deliver only the redacted `evidence/gate5-interactive-hosted-uat.json` to the independent QA reviewer. Require `PASS`, 11/11, 10 revoked sessions, per-role fixture scope, false live-funds flag, deployment ID and recorded approved change. The produced evidence intentionally marks `independent_qa_signoff=PENDING`; the reviewer must explicitly approve separately.
7. Update the canonical gate register only after the verified production run and independent approval. If the code is only present on a PR, or accounts are absent, Gate 5 is OPEN.

This is a workaround for the **GitHub Actions secret dependency only**. There is no safe workaround for authentication, access-control evidence, independent production authorization or truthful acceptance.
