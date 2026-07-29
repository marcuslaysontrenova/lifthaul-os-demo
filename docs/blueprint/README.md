# LiftHaul OS — Enterprise Product Blueprint (EPB)

> **This is the constitution of LiftHaul OS.** Every implementation decision from
> Phase 2 onward must trace back to this blueprint. Feature development is no longer
> feature-driven; it is architecture-driven. Nothing significant is built unless a
> volume below authorizes it and the Product Governance Board gate is answered.

**Status:** Phase 2 — Blueprint ratification · **Owner:** CTO / Enterprise Product Orchestrator
**Version:** 0.1 (DRAFT for CTO approval) · **Date:** 2026-07-29

---

## What LiftHaul OS is

LiftHaul OS is a **multi-tenant enterprise SaaS platform** for heavy lifting, crane,
rigging, machinery relocation, and oversized-transport operators. **RGO Machine
Rigging Services is Tenant Zero** — the first customer instance and the reference
implementation. The product must be built so that a second, third, and Nth operator
can be onboarded through configuration, not code.

This reframing is the single most important architectural fact in the blueprint: it
promotes **Organization Management, Licensing, Branding, and Identity** from
"nice-to-have admin screens" to **the foundation the entire product stands on**
(Platform 1).

## The five volumes

| Vol | Document | Governs |
|---|---|---|
| 1 | [Enterprise Product Blueprint](VOLUME_1_ENTERPRISE_PRODUCT_BLUEPRINT.md) | The 9 platforms, capability model, multi-tenancy, current-state validation, gap register |
| 2 | [Enterprise Administration Blueprint](VOLUME_2_ENTERPRISE_ADMINISTRATION_BLUEPRINT.md) | The Administration ERP: every config screen, IAM, governance, permissions |
| 3 | [UX & Design System Blueprint](VOLUME_3_UX_DESIGN_SYSTEM_BLUEPRINT.md) | Navigation, the Dialog Standard, tokens, forms, validation, accessibility, responsive |
| 4 | [Technical Architecture Blueprint](VOLUME_4_TECHNICAL_ARCHITECTURE_BLUEPRINT.md) | Domains, services, APIs, data model, integration, deployment, security, observability, scale |
| 5 | [Product Roadmap & Traceability Matrix](VOLUME_5_ROADMAP_TRACEABILITY_MATRIX.md) | Capability→platform→phase→dependency→test→acceptance; reprioritized backlog |

## The Executive Decisions (binding)

- **ED-001 — Architecture-driven.** Requests are framed as *"Build the Enterprise
  Administration Platform because the blueprint requires it,"* never *"Build User
  Management."* Capability, not feature.
- **ED-002 — Administration is its own ERP.** Not one screen — a full platform
  (Volume 2). Redesigned around the Organization / Identity / Workflow / domain-admin tree.
- **ED-003 — The Dialog Standard.** Every dialog must declare its purpose on open:
  `View` · `Edit` · `Approval Required` · `Finance Action` · `Dispatch Action`.
  Ambiguity is a defect (Volume 3 §Dialog Standard).
- **ED-004 — Configuration-First.** Default answer to "should this be coded?" is
  "can an administrator configure it instead?" Only true business logic is code.
- **ED-005 — Product Governance Board.** Every feature must answer the 10 gate
  questions (below) before it is built.

## Product Governance Board — the 10-question gate

Every future work item must answer, in its proposal, before implementation:

1. Which **platform** owns it?
2. Which **business capability** does it support?
3. Which **users** will use it?
4. Which **roles** can approve it?
5. Which **master data** does it depend on?
6. Does it need **workflow** changes?
7. Does it need **audit logging**?
8. Does it affect **reporting**?
9. Does it affect **security**?
10. Can it be satisfied by **configuration** rather than development (ED-004)?

A work item that cannot answer these is **not ready to build**. The template lives in
Volume 5 (§Governance Intake).

## How to read this in one sitting

Start here → Volume 1 (§Current-State Validation & Gap Register) to see the honest
delta between what exists today and the target. Then Volume 5 for the sequence.
Volumes 2–4 are the reference specs you consult when a work item is authorized.
