# LiftHaul AI agent handover protocol

Status: SETUP PHASE; policy supports five labels (Codex, Claude, Gemini, Grok, Kimi) but NO live automatic transfer or provider invocation exists until credentials, budgets and runners are verified.

## Shared source of truth
- GitHub issues and PRs are the task ledger; main is the integration baseline.
- Read docs/go_live/GO_LIVE_PLAN.md and current release audit before work.
- Consult the current MVP acceptance issue and open PRs before claiming a feature complete.
- Do not assume a previously discussed assistant session is available to another provider.
- Each agent records: task/issue, base SHA, branch, files modified, test commands/results, blockers, remaining steps, and next proposed action in its PR description or issue comment.

## Agent routing policy
1. Codex primary (when available).
2. Claude Code fallback for verified quota or runner unavailability.
3. Gemini CLI fallback for verified Claude quota or runner unavailability.
4. Grok fallback ONLY after independent runner authentication, availability, and budget controls are verified.
5. Kimi fallback ONLY after independent runner authentication, availability, and budget controls are verified.
6. If provider status is ambiguous, stop and request human review; never treat a failed test as a quota problem.

The quota detection mechanism must distinguish provider-specific rate/quota errors from: auth denial, broken code, failing tests, unsafe content, CI outages, and billing errors. Cross-platform usage allowance is not transferable.

## Security and coordination
- One active agent per issue; dedicated issue-named branch and concurrency lock.
- Never pass untrusted issue body/comments into a privileged shell command.
- Use least-privilege scoped tokens; store provider tokens only in GitHub Actions secrets.
- Never expose Railway production secrets or payment credentials to development agents.
- Automated agents may propose PRs. They must not merge, push to main, deploy, enable live funds, or run production-write tests.
- Release requires explicit review, checks, and the current approved release identity.
- Require budget caps and a manual kill switch before provider fallback is enabled.

## Minimum handover format
```
Issue:
Agent/provider:
Base commit SHA:
Working branch/PR:
Completed:
Files touched:
Validation performed (commands and observed results):
Known failures:
Security or production impact:
Next actionable task:
```

## Enablement checklist
- [ ] Provision Claude Code provider credentials and confirm entitlements.
- [ ] Provision Gemini provider credentials and confirm entitlements.
- [ ] Verify Grok provider API/runner authentication, terms, quota and approved budget.
- [ ] Verify Kimi provider API/runner authentication, terms, quota and approved budget.
- [ ] Confirm Codex unattended runner method (ChatGPT Work/Codex subscription limits do not automatically supply API credits).
- [ ] Decide per-provider monthly budget, timeout, and permitted scopes.
- [ ] Install and pin supported provider runners/actions with reviewed permissions.
- [ ] Add protected GitHub environment and branch rules requiring reviews/checks.
- [ ] Test synthetic quota fallback in a nonproduction branch with audit logs.
- [ ] Validate provider auth failures do NOT trigger spend on other providers.
- [ ] Enable routing only after dry-run and approval.
