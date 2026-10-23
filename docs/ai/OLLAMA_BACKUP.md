# LiftHaul Windows Ollama backup

Status: implementation prepared; Windows host connection and live model execution
are NOT verified. This is a local Codex CLI launcher, not a replacement provider
inside ChatGPT mobile, Codex Cloud, or an already-running private Codex session.
It cannot detect limits from sessions it did not launch.

## Shared alignment

Both providers receive root AGENTS.md, the same task, current issue #20/#73 bodies
and latest 20 comments per issue. The runner stops if the issue snapshot changes
before handoff. It preserves partial edits and includes current SHA/status in the
handoff; the backup must inspect the diff before continuing. Neither model's
private conversation is transferred. Report branch, SHA, tests, artifacts and
blockers in #73; #20 remains the overall ledger. Hosted Gates 5/6 stay open until
their actual acceptance evidence passes.

PR #32 is an older offline five-provider policy proposal. This local Ollama lane
does not activate that proposal or claim Claude/Gemini/Grok/Kimi access.

## Windows preparation (on Marcus's PC)

1. Install Ollama from https://ollama.com/download/windows and start it. Keep its
   API on localhost; do not open port 11434 to the internet. Install Git, Python
   3.11+ and Node.js LTS from their official sites if missing.
2. Install Codex CLI: `npm install -g @openai/codex`. Authenticate the primary CLI
   using `codex login` locally. A ChatGPT subscription is not an Ollama credential.
3. Choose and download a **local tool-capable model** appropriate to the PC's
   RAM/GPU using `ollama pull MODEL`. No model is selected automatically: hardware
   has not been inspected. `ollama list` shows exact installed names. Cloud model
   names and remote model metadata are rejected by this launcher.
4. Ollama recommends at least 64k context for Codex. Configure a context the PC
   can actually support, then verify with `ollama ps` during an inference. A model
   metadata check does not prove sufficient memory, context or coding ability.
5. Fetch the reviewed backup PR branch into the canonical checkout, then create
   your own `work/...` branch from it. Run only in a nonproduction development
   checkout with synthetic data and no production credentials/MCP tools. Stop
   separately running agents in this checkout. Do not change main or merge this
   setup merely to run locally.

From that repository root in PowerShell (replace MODEL with its exact name):

```powershell
python scripts/ai/ollama_backup.py --model MODEL --check
```

This checks repository identity/branch, CLI installation, localhost API and tool
capability only. No install, model download, inference, billing or production change
is performed by the check.

## Start a task with automatic quota handoff

Create a plain UTF-8 task file outside the repository, containing the specific
issue #73 development task and acceptance criteria. Never include credentials.
Use its absolute path:

```powershell
.\scripts\ai\Start-LiftHaulAI.ps1 -Model MODEL -TaskFile C:\LiftHaulTasks\current-task.txt
```

The launcher runs `codex exec --json --sandbox workspace-write` using the current
primary configuration. On nonzero exit plus a recognized CLI quota error event,
it runs a fresh `codex exec --oss --local-provider ollama -m MODEL` with the shared
checkpoint. Auth, billing, test, network, unknown failures and timeouts do not
switch providers. Ollama gets one attempt, not an endless retry loop. Some CLI
versions may use an unrecognized quota schema: they stop until explicitly tested.

Sandbox flags and instructions do not remove inherited credentials or MCP access;
use the isolated development checkout described above. The launcher invokes no
push/merge/deploy itself. Review generated changes and run tests before publishing.

## Verify before calling it connected

- On the Windows PC, record installed CLI/Ollama versions and local model name.
- Run a separate harmless local smoke task with
  `codex exec --oss --local-provider ollama -m MODEL -c model_reasoning_effort=none --sandbox read-only "Read AGENTS.md and summarize the Gate 5/6 constraints. Do not edit files."`
- Verify it actually runs on local hardware (`ollama ps`) and follows the shared
  contract. Test a bounded development coding task and its regression checks.
- Run the launcher through a genuine quota failure and verify one handoff, preserved
  edits, correct branch/SHA and no duplicate agent. Do not fabricate a limit result.
- Only then mark local inference/handoff VERIFIED in #73. Keep hosted acceptance
  separate. A successful agent exit does not prove its code/tests or MVP acceptance.

### Non-thinking-model compatibility

A non-thinking local model can reject requests with `does not support thinking`
when a client sends reasoning effort. Codex CLI 0.163.0 does not recognize the
legacy `model_supports_reasoning_summaries` configuration override; do not rely
on that key to suppress thinking.

The local launcher now supplies `-c model_reasoning_effort=none` for the backup
process only, leaving the primary's settings unchanged. Codex's tagged 0.163.0
source accepts the None effort and Ollama's compatibility layer maps `none` to
`think=false`. The correction has offline regression coverage; the corrected live
request must be verified on the operator's host before activation. Model-catalog
decode warnings require diagnosis if they block execution. Keep private device,
capacity and runtime observations out of public issues, PRs and documentation.

Version source checks:
- https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/config/src/config_toml.rs
- https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/protocol/src/openai_models.rs
- https://github.com/ollama/ollama/blob/main/openai/openai.go

Sanitized execution status is stored under `.git/lifthaul-ai/last-status.json`,
outside tracked source. Raw provider output and prompts are not logged. If the
launcher stops with a lock, verify no agent is running before removing the stale
`.git/lifthaul-ai/runner.lock`. Preserve changes on interruption; do not reset them.

## Offline validation

`python -m unittest scripts.ai.test_ollama_backup -v`

Sources checked 23 October 2026:
- https://docs.ollama.com/integrations/codex
- https://docs.ollama.com/windows
- https://learn.chatgpt.com/docs/config-file/config-advanced
- https://learn.chatgpt.com/docs/developer-commands?surface=cli
