# PHASE 9 — AI Migration Report

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-03
**Scope:** introduce governed AI Administration WITHOUT relabeling any human-authored record as
AI-generated, and WITHOUT changing any historical financial value or operational status.

## Migration strategy (greenfield, additive, advisory-only)

There is **no existing AI/LLM code** in the repository (confirmed by the capability audit — no prompts,
model calls, embeddings, vector store, or generated content; the only reference is a placeholder
`ai_assistance` module toggle with no implementation). Phase 9 is therefore **greenfield governed AI**:
nothing to migrate, nothing to relabel. Existing deterministic capabilities remain **rule-based** and
are **not** relabeled as AI.

| Existing capability | Actual nature | Migration action |
|---|---|---|
| Customer duplicate detection (Phase 3) | deterministic string matching | **kept RULE-BASED** — not relabeled as AI |
| Risk / credit classification (master data) | admin-configured lookups | **kept RULE-BASED** |
| Carrier / resource eligibility (ops) | deterministic capacity/status checks | **kept RULE-BASED** |
| Governed report metrics (Phase 8) | declarative SQL aggregation | **kept RULE-BASED** |
| `ai_assistance` module entry | placeholder toggle (no code) | left as a module toggle; now backed by governed AI when enabled |

## Classification of existing "AI-adjacent" functions

| Class | Items |
|---|---|
| governed AI | 0 existing (all new Phase-9 use cases are additive) |
| deterministic rule | duplicate detection, eligibility, report metrics (kept) |
| approved heuristic | 0 |
| experimental / deprecated / prohibited | 0 |
| excluded | numbering counts, data-integrity checks |

## Migration results

| Metric | Result |
|---|---|
| Existing AI functions found | **0** |
| Governed AI use cases (new, additive) | ~9 (advisory + human-reviewed) |
| Functions relabeled as AI | **0** |
| Human-authored records relabeled as AI-generated | **0** |
| **Financial differences** | **0** |
| **Operational status differences** | **0** |
| **AI-authored record changes** | **0** |

## Invariants (proved in CI on PostgreSQL)

```
UNEXPECTED FINANCIAL DIFFERENCES = 0
UNEXPECTED OPERATIONAL STATUS CHANGES = 0
UNEXPECTED AI-AUTHORED RECORD CHANGES = 0
```

- **AI-authored records:** AI output NEVER auto-commits to an authoritative record; every `execute`
  returns an advisory result with `committed=False` and a human-review requirement. Only a human review
  (accept/edit/reject) influences anything, and edits stay distinguishable from the model output.
- **Financial:** `test_ai_does_not_change_financials` drives an AI execution against a booking and
  asserts the quotation `tax`/`total` unchanged (72000/672000).
- **Operational:** AI never changes a transaction status; prohibited actions are blocked + tested.

## Live AI boundary (§31)

Live AI is **BLOCKED**: the real OpenAI/Anthropic adapters read their API key from the environment
(server-side only, via the Phase-6 secret reference) and, absent owner-controlled credentials, report
BLOCKED without fabricating success. All non-secret capability is proven with the deterministic mock.
Owner actions to unblock (production readiness reported SEPARATELY):

1. Provision an OpenAI or Anthropic API key and store it as the env-backed secret
   (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`).
2. Register + approve the real model in the model registry (approved environment SANDBOX first).
3. Run a controlled non-production execution; confirm data-retention/residency terms.
4. Only then may LIVE AI PRODUCTION READINESS be marked VERIFIED.

## Reversibility

- Only additive DDL; no column drops; no historical record touched or relabeled.
- Use cases / prompts / models can be disabled or kill-switched (fail-safe) without data loss.
- All AI executions are advisory rows; deleting them affects no authoritative record.
