# Volume 3 — UX & Design System Blueprint

> Navigation, dialog standards, forms, validation, accessibility, responsive
> behavior, and interaction patterns. The centerpiece is the **Dialog Standard
> (ED-003)**: users must never have to guess whether a dialog is informational,
> editable, or an action. Version 0.1 (DRAFT).

---

## 1. Design principles

1. **Purpose is declared, never inferred.** Every surface states what it is and what
   it will do (ED-003).
2. **One system, end to end.** The public site, the operational console, and the
   Administration ERP share one token set, one component library, one interaction
   grammar.
3. **Configuration is visible.** Where a value comes from config (ED-004), the UI can
   show its source (platform/tenant/unit/user) on request.
4. **Accessible by default.** WCAG 2.2 AA is the floor, not a feature.

## 2. Design tokens (semantic, tenant-themeable)

The brand tokens already in the RGO demo become the **primitive** layer; semantic
tokens map to them so a second tenant re-themes by swapping primitives only.

| Semantic token | RGO (tenant 0) value | Usage |
|---|---|---|
| `--brand` | `#5FBE2A` (apple green) | primary brand fields, hero |
| `--brand-ink` | `#0C2306` | text on brand, dark surfaces |
| `--accent` | `#F2611A` (orange) | primary actions, links, emphasis |
| `--accent-2` | `#F2C21A` (gold) | highlights, hazard motif |
| `--surface` / `--surface-2` | white / near-white | cards, panels |
| `--footer-bg` | `#0A1705` | dark footer/CTA |
| `--line` | subtle neutral | borders, dividers |
| Status | `--ok`, `--warn`, `--danger`, `--info` | validation, mode chips |

Tokens are the **only** source of color/spacing/type. No literal hex in components
(the demo's inline styles are migrated to tokens as part of G-06).

**Spacing scale:** 4·8·12·16·24·32·48·64. **Radius:** 8/11/999. **Type:** Inter;
scale 12/13.5/15/17/20/24/32/44 with weights 400/600/700/800.

## 3. The Dialog Standard (ED-003) — binding

Every dialog **declares a mode** via a mandatory header chip and matching affordances.
The mode determines color, allowed controls, confirmation, and audit behavior.

| Mode | Chip | Color | Controls | Confirmation | Audit |
|---|---|---|---|---|---|
| **View** | `VIEW` | neutral/steel | read-only; no inputs; "Close" only | none | read (optional) |
| **Edit** | `EDIT` | brand green | inputs enabled; "Save"/"Cancel"; dirty-guard | on unsaved-close | `*_UPDATED` |
| **Approval Required** | `APPROVAL` | gold | approve/reject + reason; maker≠checker enforced | explicit approve/reject | `*_APPROVED/REJECTED` |
| **Finance Action** | `FINANCE` | orange | money fields; verification; idempotency key | explicit + amount echo | `PAYMENT_*` / `INVOICE_*` |
| **Dispatch Action** | `DISPATCH` | blue | resource/schedule; conflict guard | explicit + conflict warning | `RESERVATION_*` / `JOB_*` |

**Rules:**
- The chip is **top-left in the dialog header**, always visible, never truncated.
- A dialog is **exactly one mode**. Need to edit from a View? An explicit "Edit"
  action re-opens in Edit mode (state transition, not an in-place toggle).
- Destructive/irreversible controls (send, confirm, reject, allocate, delete) use the
  accent/danger treatment and require explicit confirmation echoing the object.
- Action modes (Approval/Finance/Dispatch) **must** show the governing rule ("Approval
  required: amount ≥ ₱500,000 → Approver") sourced from Workflow Admin (ED-004).

**Reference header markup (pattern, tokenized):**
```html
<header class="dlg-head" data-mode="finance">
  <span class="mode-chip">Finance Action</span>
  <h2 id="dlg-title">Verify downpayment</h2>
  <p class="dlg-rule">Rule: verified amount must equal requested (idempotent)</p>
</header>
```
`data-mode` drives all mode styling from tokens; screen readers announce the mode via
`aria-describedby` pointing at `.mode-chip` + `.dlg-rule`.

## 4. Navigation architecture

Three top-level surfaces, one shell:

- **Public site** (marketing + online booking) — the current RGO front-end, retokenized.
- **Operational Console** — Commercial + Operations + Financial + Analytics day-to-day.
- **Enterprise Administration** — the Volume 2 ERP; a distinct left-nav tree, visually
  differentiated (darker chrome) to signal "you are configuring the product."

Navigation model: persistent left sidebar (platform → module → screen), breadcrumb,
and a command palette (`Ctrl/Cmd-K`) for direct capability jump. Admin is reachable
only with `*.admin`/`iam.*` permissions and is clearly labeled as governance space.

## 5. Forms & validation standard

- **Field contract:** label, help text, validation state, and (if configured) the
  config source badge. Required fields marked; optional labeled "(optional)".
- **Validation tiers:** inline (on blur) → form (on submit) → server (authoritative).
  Server errors map back to fields by name.
- **Money & quantities:** typed inputs, locale/currency from Org profile, never free
  text; negative-value guards match backend (e.g., quotation line validation).
- **Dirty-state guard:** Edit-mode dialogs warn on unsaved close.
- **Idempotency:** Finance actions carry a client-generated idempotency key surfaced
  in the confirm step.

## 6. Accessibility (WCAG 2.2 AA floor)

- Full keyboard operability; visible focus rings (token `--focus`); logical tab order.
- Dialogs: focus trap, `role="dialog"`, `aria-modal`, ESC to cancel (non-action modes),
  restore focus on close.
- Contrast: all text ≥ 4.5:1; the mode chips carry a text label (not color alone) so
  mode is conveyed without color perception.
- Motion: respect `prefers-reduced-motion`; hover-shift/transform effects disabled.

## 7. Responsive behavior

- Breakpoints: ≥1200 (desktop console), 820 (tablet — admin tables become stacked
  cards), 460 (mobile — single column; action dialogs full-screen).
- Data tables: horizontal scroll within their own container; the page body never
  scrolls sideways.
- The public site is mobile-first (already responsive); console/admin are
  desktop-first but degrade to usable mobile for approvals and field actions (→ P8).

## 8. Component library (v1 scope)

Shell (sidebar, topbar, breadcrumb, command palette) · Mode-aware Dialog · Data Table
(sort/filter/paginate, scoped scroll) · Form controls (text, money, select, date,
toggle, multiselect) · Status chips · Approval panel · Audit timeline · KPI tile ·
Empty/skeleton/error states · Toast/notification. Each component: tokenized, themeable,
accessible, documented with the modes it supports.

## 9. UX debt to retire (from current build)

- **Inconsistent dialogs** (the trigger for ED-003) → adopt the Dialog Standard.
- **Inline hex/spacing** in `index.html` → migrate to tokens.
- **localStorage as data** → API-backed state (G-06); UI shows real persistence.
- **No config-source visibility** → add the source badge where values are configurable.

## 10. Acceptance criteria for the design system

1. Every dialog in console + admin renders a correct mode chip; a lint/test flags any
   dialog without a declared mode.
2. A tenant re-theme is achieved by swapping primitive tokens only (RGO → second brand)
   with zero component edits.
3. Automated a11y checks (axe) pass at AA on the shell, a representative Edit dialog,
   and an Action dialog.
4. No component contains a literal color or spacing value outside the token set.
