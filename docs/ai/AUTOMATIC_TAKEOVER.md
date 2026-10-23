# Automatic Codex to Antigravity patch takeover

This is an executable quota transition, not a provider registry. Launch the task
through `scripts/ai/automatic_takeover.py`; it cannot intercept an unrelated Codex
desktop session, this chat, or a Codex process started outside the runner.

## One-time local verification

Exit all interactive Antigravity sessions first. Do not start another session
while this runner owns the CLI preferences. In PowerShell at the work checkout:

```powershell
python scripts/ai/automatic_takeover.py --verify-takeover
```

Use a current stable Antigravity CLI. Its changelog records denial reporting fixes
in 1.1.27. Denied tool steps can terminate as `ERROR`, not `DONE`, and successful
process exit alone is not proof of task success. The guard recognizes explicit
permission-error tool steps, command-specific `denied_actions`, or the CLI's
specific command-permission diagnostic on stderr; model response text is never
used as denial proof. Unknown or successful tool execution still blocks.

This sends real requests to the locally authenticated Antigravity CLI. It tests a
harmless prohibited command, alignment, and an actual small file edit performed by
the host on a temporary fixture. It does not exhaust Codex quota or certify hosted
acceptance. Success is `LOCAL_TAKEOVER_SIMULATION_PASS_REAL_QUOTA_UNVERIFIED`.
An absent or differently shaped permission-denial event fails closed, rather than
guessing that the installed CLI enforced its settings. Real local verification
has to be run on the machine with the authenticated CLI; offline mocks are not
a substitute. Never enable paid overage or purchase credits for this test.

## Start a task with automatic fallback

Create a UTF-8 JSON task file containing exactly these two keys, choosing actual
existing source files for the task:

```json
{
  "task": "Describe one bounded coding change and preserve existing partial work.",
  "files": ["cargo-booking.js", "book.html"]
}
```

```powershell
python scripts/ai/automatic_takeover.py --task-file path/to/task.json
```

The runner starts Codex normally. Only a recognized CLI quota event following a
nonzero exit triggers Antigravity. Authentication, network, billing, ambiguous
errors and timeouts stop. A changed branch, commit, shared contract or issue
snapshot stops the transition. There is one fallback attempt, never a retry loop.

Antigravity receives the complete shared contract, issue snapshot and selected
files as they exist AFTER Codex stops, including its partial edits. Stdin carries
the prompt so Windows argument length does not truncate it. The fallback has no
model tool permissions. It produces structured full-file edits; the host validates
every path and original hash before applying any file. It never executes model
commands. There is no direct fallback tool execution, push, merge or deployment.
Only 1-12 existing UTF-8 files, each <=50 KB and combined handoff <=180 KB, qualify.
Oversized input stops without truncation. Controls, infrastructure, hidden paths,
credentials, scripts, CI and docs are outside this first patch lane. New files and
deletions are not supported. Do not include secrets in selected source files.

A completion status means a patch was applied; tests, correctness and hosted
acceptance remain unverified. Run appropriate reviewed tests separately. The
fallback cannot automatically run arbitrary tests since that would grant shell
execution to generated code. The old Ollama runner and disabled Grok lane remain
separate; this implementation does not claim an automatic five-provider chain.

## Permission lifecycle and recovery

Antigravity documents fine-grained permissions in the noncredential preferences
file `~/.gemini/antigravity-cli/settings.json`. The runner temporarily replaces its
permission lists with deny-all model actions and `request-review`. It does not
read credential stores or change billing. Other preferences are preserved. This
is a model permission boundary, not a claim of OS containment or no provider-owned
logs. Never run an external agent concurrently; the runner checks active CLI
processes and holds both a checkout lock and a global preferences lock. External
processes do not honor those locks, so operators must keep other sessions closed.

When preferences are otherwise unchanged, the exact previous bytes are restored
after success or an ordinary exception. JSON formatting and rule order are not
permission changes. If the CLI saves unrelated preferences, only the runner's
three owned fields are restored, preserving those unrelated updates. A private
recovery copy is retained beside settings until restoration finishes. If effective
permission fields change, the runner stops and retains recovery. A killed process may leave restrictive settings
and locks; verify it is stopped before manually reconciling the recovery file and
removing locks. Never blindly delete a lock or overwrite concurrent preference
changes. Do not upload recovery files, logs, account identities or raw provider
output. No automatic global permissions are relaxed.
An omitted/null default `toolPermission` preference is accepted only with the full
deny profile intact. CLI stream initialization must still report `request-review`,
and the command challenge must show an actual denial before alignment or patch
work. An explicitly empty advertised tools list with no tool steps is a separate
acceptable capability check. A missing tools list or a model's textual refusal
does not count as proof; any successful command output blocks the run.

After all Antigravity sessions are closed, recover a stopped run with:

```powershell
python scripts/ai/automatic_takeover.py --recover-settings
```

Recovery makes no model request. It restores only the runner's owned fields when
the deny profile is still intact; a real policy change remains blocked for manual
reconciliation. It never blindly copies the old settings over new preferences.

## Validation limits

Offline tests exercise quota routing, partial-work preservation, structured
output, settings restoration and concurrent changes. Until the installed CLI
passes the denial challenge and bounded live patch test, local automatic takeover
is unverified. A real quota event is separately reported only on a real task run.
Gate 5 and Gate 6 remain OPEN; MVP NO-GO.

Official protocol and permission references:
- https://antigravity.google/docs/cli/headless/
- https://antigravity.google/docs/permissions/
- https://antigravity.google/docs/settings?tab=cli
