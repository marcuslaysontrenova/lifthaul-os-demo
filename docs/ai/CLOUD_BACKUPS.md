# Gemini and Grok backup setup

Status: staged readiness configuration, not active multi-provider execution.
`BACKUP_PROVIDERS.json` describes desired Codex -> Gemini -> Ollama -> Grok order.
It does not replace the existing Codex -> Ollama executable launcher. Gemini has
a read-only alignment adapter here; automatic Gemini quota handoff and a Grok
execution adapter are NOT implemented. Never treat this registry as a live router.

Use root AGENTS.md, issue #73 work order and issue #20 release ledger across all
providers. GEMINI.md imports the shared contract. GROK.md is a manual/future-adapter
handoff file, not an automatically loaded configuration. Keep keys, account
identities, device metadata and raw runtime output out of public artifacts.

## Gemini on Windows

In a separate PowerShell window, install the official stable client:

```powershell
npm.cmd install -g @google/gemini-cli
gemini.cmd
```

Use browser-based Sign in with Google locally. Enter credentials and codes only
in Google's own login pages. Individual Google sign-in has a free quota; eligibility
and limits are account-specific. Headless mode can reuse cached authentication.
Do not enable paid overage, buy credits or assume a web subscription proves API access.
Set the CLI's billing overage strategy to never in its own settings if offered.
The probe does not change account or global CLI settings and cannot attest the
account has no credits; verify free-quota mode locally before invoking it.

After the current launcher finishes, update the work checkout and run:

```powershell
python scripts/ai/cloud_backup_readiness.py
python scripts/ai/cloud_backup_readiness.py --verify-gemini
```

If setup is blocked, `--verify-gemini` prints named checks rather than a generic
guess: Google sign-in environment mode, canonical work branch, CLI discovery,
Windows npm entrypoint and launcher lock. Run `--diagnose` for those checks alone,
without model requests. No credentials, paths, account IDs or provider output are
printed. A lock must never be removed until its owning process is verified stopped;
the checker does not remove locks or reset authentication/configuration.

Windows npm launchers use the installed `@google/gemini-cli/package.json` `bin`
mapping, supporting both bundled and unbundled layouts rather than guessing a
single JS path. The entry must stay inside the named package and exist; Node is
resolved beside the launcher or on PATH. No command-shell interpolation is used.

The first command is offline readiness only. The second makes one Gemini CLI
request using plan (read-only) mode and explicitly supplies AGENTS.md. It validates
the same alignment fields as Ollama, rejects tool events and does not execute
model-produced instructions. Requires already-cached local Google sign-in; it
rejects obvious API-key/Vertex environment modes. It shares the existing launcher
lock; do not interrupt another active agent or run two agents in one checkout.
Successful alignment does not enable automatic task execution or certify safety.

## Grok

The fixed API base URL and key environment-variable NAME are staged in the registry.
No key is read, stored, copied or sent by this readiness tool. No Grok calls occur.
Official API onboarding requires an API account, credits and an API key. A chat
login does not establish API authentication. Before paid integration, require an
explicit budget cap and authorized secure local credential setup. Then implement
and validate the scoped adapter, common alignment, isolation, quota handling and
bounded coding task. Keep this provider disabled until all are proved.

## Local alternatives to evaluate

- `qwen3.5:4b`: open-source Apache 2.0 family; tools/vision/thinking in Ollama.
- `qwen3:4b`: Apache 2.0; tools/thinking; a smaller local candidate.

These are candidates, not approved replacements. Download size is not runtime RAM.
Keep only one loaded model during capacity tests. Context, instruction following,
tool behavior and bounded coding must pass before any automatic handover.

Sources checked 23 October 2026:
- https://geminicli.com/docs/get-started/installation/
- https://geminicli.com/docs/get-started/authentication/
- https://geminicli.com/docs/cli/headless/
- https://geminicli.com/docs/cli/plan-mode/
- https://geminicli.com/docs/resources/quota-and-pricing/
- https://docs.x.ai/developers/quickstart
- https://docs.x.ai/developers/pricing
- https://ollama.com/library/qwen3.5:4b
- https://ollama.com/library/qwen3:4b
