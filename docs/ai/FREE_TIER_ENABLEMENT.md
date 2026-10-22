# Free-tier enablement: no paid subscription assumed

## What is implemented
Shared instructions for Codex, Claude and Gemini; documentation-only readiness GitHub workflow in PR #32. This is NOT an operational automatic fallback.

## How to prepare Gemini at no upfront subscription cost
1. On a trusted developer computer, install Gemini CLI using Google's current official installation instructions.
2. Sign in to Gemini CLI with your own eligible Google account using its interactive OAuth flow, or provision an eligible Gemini API key in Google AI Studio under published free-tier terms.
3. Run a read-only test against a fresh clone of this repository and verify GEMINI.md is read; request an issue #20 gap analysis with citations to files and a test plan.
4. For unattended GitHub Actions, use a supported noninteractive provider auth method kept in GitHub Actions secrets, NOT a pasted personal OAuth token. Verify free-tier/API eligibility, spending controls and provider license/terms before enabling. No secret should be inserted into repository content.
5. Confirm hard request limits and zero/acceptable cost before running any unattended agent loop.

## Claude
Claude Free chat is suitable for manual code review/handover using pasted non-sensitive changes, subject to its limits. It does NOT provide Claude Code automation by default. Leave Claude runner disabled unless a separately supported Claude Code entitlement/API credential is available.

## Codex
ChatGPT Codex/Work session limits do NOT translate into unattended Codex API entitlements. Do not configure API fallback without separate auth and cost verification.

## Routing activation criteria
- Credentials configured by owner, never via chat;
- budgets/rate limits understood for each provider;
- tightly scoped workflows with restricted permissions and nonproduction branches;
- mock failover simulation demonstrates *quota-only* fallback, auditable handover and no retry loops;
- source tests / CI checks and security review are green;
- owner explicitly enables the runner after review.

Until then, switching is manual, documented, and safe. Keep MVP issue #20 authoritative.

## Grok / Kimi (planned; not connected)
- Grok (xAI) and Kimi (Moonshot AI) are now represented in the OFFLINE policy only. Neither model's free chat access is proof of API credits, automated runner access, or safe unattended coding permissions.
- Verify each provider's official API terms, accessible endpoints, supported structured outputs, tool execution requirements, regions, data processing policy, account eligibility, quotas, and charges at the time of provisioning.
- Create separate, least-privilege provider credentials only after owner approval; store in protected nonproduction GitHub Actions secrets, not in source files or chat.
- Keep runtime invocations disabled until sandbox authentication, budgets, per-task spending ceilings, offline and limited live test evidence, a manual kill switch, and PR-review protections pass.
- **ChatGPT/Codex quota cannot be directly observed by a GitHub workflow without an independently supported API or runner-specific quota signal.** Do not claim a cross-chat automatic handover; GitHub's issue/PR evidence is the transferable context, not an active model session.
