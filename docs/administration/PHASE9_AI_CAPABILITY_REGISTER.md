# PHASE 9 — AI Capability Audit Register

**Product:** LiftHaul OS · **Tenant Zero:** RGO Machine Rigging · **Date:** 2026-08-03
**Scope:** governed AI Administration — use cases, model registry, prompt versioning, human review,
guardrails, cost controls, safety, kill switch, audit. Deterministic mock provider; live AI blocked.
**Method:** full repository inspection for AI code, prompts, model calls, embeddings, vector stores,
recommendations, generated content, classification, provider secrets, and human-review requirements.

> Guiding rules (directive): **do not relabel ordinary business rules as AI**; AI is **advisory and
> human-reviewed by default**; AI must NEVER autonomously release funds, verify payments, approve
> refunds/quotations, change financial records, dispatch, publish external communications, delete
> records, elevate roles, retrieve secrets, or access another tenant. Reuse Phase-6 secret references,
> Phase-5 sensitivity, Phase-1 tenant isolation, Phase-8 governed report results (for grounding).

## Classification legend

`GOVERNED AI` · `RULE-BASED` · `MACHINE LEARNING` · `GENERATIVE AI` · `HEURISTIC` · `MOCK` ·
`EXPERIMENTAL` · `UNVALIDATED` · `PROHIBITED` · `RETIRED` · `NOT APPLICABLE`.

---

## A. Existing "AI-adjacent" capabilities (all deterministic — NOT AI)

| Capability | Code location | What it actually is | Human review? | Automated action | Class |
|---|---|---|---|---|---|
| Customer duplicate detection | `crm_admin.py` (Phase 3) | exact/normalized/weighted string matching (deterministic) | merge needs approval | none auto | **RULE-BASED** (not AI) |
| Risk / credit classification | `masterdata.py` master data | admin-configured lookup values | — | none | **RULE-BASED** |
| Carrier / resource eligibility | `ops.py` reservations/gates | deterministic capacity/status checks | — | none | **RULE-BASED** |
| Governed report metrics | `reporting.py` (Phase 8) | declarative SQL aggregation | — | none | **RULE-BASED** |
| `ai_assistance` module entry | `settings.py:144` module registry | placeholder module toggle (no code) | — | none | **NOT APPLICABLE** (placeholder) |
| PDF generation | `pdfgen.py` | deterministic templating | — | none | **NOT APPLICABLE** |

**Finding:** there is **no existing LLM/AI provider code, no prompts, no model calls, no embeddings, no
vector store, no AI-generated content**. The only "AI" reference is a placeholder `ai_assistance` module
toggle with no implementation. Phase 9 is therefore **greenfield governed AI** — nothing to relabel.

## B. Provider secrets

| Item | Present? | Note |
|---|---|---|
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | **NO** | none configured → live AI BLOCKED (owner action required) |
| Any model-provider secret | **NO** | Phase 6 secret-reference boundary reused when the owner provisions one |

## C. Phase-9 governed AI capabilities (NEW — all advisory + human-reviewed)

| Use case | Risk | Grounding | Human review | Automated action |
|---|---|---|---|---|
| booking summary / missing-info / clarification questions | low | booking record | required-for-external only | none (advisory) |
| cargo classification suggestion | low | booking + master data | accept/edit/reject | none (suggestion only) |
| vehicle-category suggestion | low | booking | accept/edit/reject | none |
| quotation-assumption review / missing cost components | medium | quotation snapshot (read-only) | required | **none — never touches authoritative totals** |
| delayed-job / dispatch-exception summary | low | governed ops data | advisory | none |
| incident preliminary-severity + missing-evidence | medium (SAFETY) | incident record | required | **none — never determines final liability** |
| governed report summary / KPI-movement explanation | low | Phase-8 governed report output only | advisory | none |
| customer-response draft | medium | conversation history | **required before send** | none — human publishes |
| document field extraction / type classification | medium | uploaded doc (untrusted) | **verification required before authoritative** | none |

## D. Prohibited AI actions (enforced — tool registry can never contain these)

payment release · live payment initiation · fund release · Wise settlement verification · refund
approval · tax-treatment change · downpayment-policy change · invoice modification · cargo-legality
confirmation · unverified-carrier activation · claims-liability decision · disciplinary action · final
hiring · access denial by profiling · legal/contract publication · security-config modification · audit
disablement · record deletion · cross-tenant access without governed elevation.

## Summary counts

- **Existing AI capabilities:** **0** (no LLM/prompt/model/embedding code) — nothing to migrate/relabel.
- **RULE-BASED (kept, NOT relabeled as AI):** duplicate detection, eligibility, report metrics.
- **NEW GOVERNED AI use cases (advisory + human-reviewed):** ~9 low/medium-risk.
- **PROHIBITED actions (enforced):** ~19 — never available as AI tools.
- **Provider:** DeterministicMockProvider for all CI; live OpenAI/Anthropic **BLOCKED** (no credentials).

## Safety commitments

1. AI is **advisory** — output NEVER auto-commits to an authoritative record; a human accepts/edits/
   rejects → **UNEXPECTED AI-AUTHORED RECORD CHANGES = 0**.
2. AI never touches financial totals/tax/downpayment/payment status → **UNEXPECTED FINANCIAL
   DIFFERENCES = 0**; never changes operational status → **UNEXPECTED OPERATIONAL STATUS CHANGES = 0**.
3. The tool registry is **allowlisted**; prohibited actions cannot be registered or executed (enforced +
   tested).
4. Secrets/payment credentials/auth tokens are **never** sent to a provider (data classification +
   redaction); provider keys use Phase-6 secret references and never reach the browser.
5. Prompt-injection: documents/customer messages are **untrusted**; instructions inside them cannot
   override system/business policy; tenant scope + tool allowlist + output validation + human review.
6. Live AI production readiness is reported **separately** and is not claimed without a real provider test.
