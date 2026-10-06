"""LiftHaul Revenue & Compliance DNA.

One governed registry sits above every monetisation engine.  It does not duplicate
pricing, invoices, payment rails, subscriptions, rentals, or referral modules.
Instead it answers the question that those engines cannot answer on their own:

    May LiftHaul lawfully activate this revenue stream, on this evidence, now?

Every seeded scheme is non-active by default.  A scheme moves through evidence
submission, independent verification, independent approval, and activation.  The
runtime guard can be placed in ``observe`` mode during migration and ``enforce``
mode in production through REVENUE_DNA_ENFORCEMENT.

This is a compliance workflow, not legal advice.  The legal-basis references are
primary-source pointers for counsel/compliance review; they are never represented
as automatic government approval.
"""
from __future__ import annotations

import datetime
import json
import os

import core


SCHEME_STATUSES = (
    "ASSESSMENT", "BLOCKED", "READY_FOR_APPROVAL", "APPROVED",
    "ACTIVE", "SUSPENDED", "RETIRED",
)
CONTROL_STATUSES = ("OPEN", "IN_REVIEW", "VERIFIED", "REJECTED", "NOT_APPLICABLE")
EVIDENCE_STATUSES = ("SUBMITTED", "VERIFIED", "REJECTED", "EXPIRED")
PAYMENT_DNA_SCHEME = "payment_admin_fee"


SCHEMA = """
CREATE TABLE IF NOT EXISTS revenue_schemes(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  category TEXT NOT NULL, description TEXT, applicability TEXT,
  launch_phase TEXT NOT NULL DEFAULT 'PHASE_1', regulatory_level TEXT NOT NULL,
  pricing_model TEXT, existing_module TEXT, owner_role TEXT,
  legal_basis TEXT, prohibited_claims TEXT, status TEXT NOT NULL DEFAULT 'ASSESSMENT',
  version INTEGER NOT NULL DEFAULT 1, notes TEXT,
  created_by INTEGER, created_at TEXT, updated_by INTEGER, updated_at TEXT,
  submitted_by INTEGER, submitted_at TEXT, approved_by INTEGER, approved_at TEXT,
  activated_by INTEGER, activated_at TEXT, suspended_by INTEGER, suspended_at TEXT);

CREATE TABLE IF NOT EXISTS revenue_controls(
  id INTEGER PRIMARY KEY, scheme_id INTEGER NOT NULL REFERENCES revenue_schemes(id),
  code TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL,
  description TEXT, required INTEGER NOT NULL DEFAULT 1, owner_role TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN', evidence_id INTEGER,
  verified_by INTEGER, verified_at TEXT, notes TEXT,
  UNIQUE(scheme_id, code));

CREATE TABLE IF NOT EXISTS revenue_evidence(
  id INTEGER PRIMARY KEY, scheme_id INTEGER NOT NULL REFERENCES revenue_schemes(id),
  control_id INTEGER NOT NULL REFERENCES revenue_controls(id),
  evidence_ref TEXT NOT NULL, description TEXT, issued_by TEXT,
  issued_at TEXT, expires_at TEXT, status TEXT NOT NULL DEFAULT 'SUBMITTED',
  submitted_by INTEGER, submitted_at TEXT, verified_by INTEGER, verified_at TEXT,
  rejection_reason TEXT);

CREATE TABLE IF NOT EXISTS revenue_activation_log(
  id INTEGER PRIMARY KEY, scheme_id INTEGER NOT NULL REFERENCES revenue_schemes(id),
  action TEXT NOT NULL, from_status TEXT, to_status TEXT, actor_id INTEGER,
  reason TEXT, readiness_snapshot TEXT, created_at TEXT);
"""


DTI_ITA = "https://ecommerce.dti.gov.ph/implementing-rules-and-regulations/"
DTI_TRUSTMARK = "https://trustmark.dti.gov.ph/guideline"
BIR_MARKETPLACE = "https://bir-cdn.bir.gov.ph/BIR/pdf/RMC%20No.%208-2024%20%281%29.pdf"
BSP_PAYMENTS = "https://www.bsp.gov.ph/SitePages/PaymentsAndSettlements/PaymentsAndSettlements.aspx"
LTFRB = "https://ltfrb.gov.ph/"
NPC = "https://privacy.gov.ph/"
DOLE_HEAVY = "https://oshc.dole.gov.ph/accreditation/"
IC_REFERRAL = "https://www.insurance.gov.ph/wp-content/uploads/2025/10/IC-Legal-Opinion-No.-2025-03_Request-for-Opinion-on-Referral-Fee_LICD.pdf"


# No rate is made live by this seed.  Commercial numbers remain in their existing
# governed engines and need their own approval/versioning.
SCHEMES = [
    ("provider_success_fee", "Provider success fee", "MARKETPLACE",
     "A disclosed fee charged to a provider after a completed eligible service.",
     "Core booking marketplace", "PHASE_1", "STANDARD",
     "Percentage or fixed fee; configured in versioned marketplace fee policy.",
     "saas / platform_fee_settlement", "Commercial + Tax"),
    ("customer_platform_fee", "Customer platform fee", "MARKETPLACE",
     "A separately disclosed booking or technology fee charged to the customer.",
     "Consumer and enterprise bookings", "PHASE_1", "STANDARD",
     "Fixed, percentage, or tiered fee shown before confirmation.",
     "saas / billing", "Commercial + Consumer Protection"),
    ("fleet_subscription", "Fleet subscription", "SUBSCRIPTION",
     "Recurring access to provider fleet, dispatch, compliance and reporting tools.",
     "Verified carriers and owner-operators", "PHASE_1", "STANDARD",
     "Monthly or annual plan with immutable plan versions.",
     "saas", "Commercial + Tax"),
    ("enterprise_subscription", "Enterprise subscription", "SUBSCRIPTION",
     "Contracted enterprise workspace, reporting, controls and support.",
     "Corporate shippers and managed accounts", "PHASE_1", "STANDARD",
     "Contracted monthly/annual subscription and approved add-ons.",
     "saas / billing", "Enterprise Commercial"),
    ("heavy_equipment_rental", "Heavy-equipment coordination and rental fee", "RENTAL",
     "Coordination or platform fee for verified equipment, operator and project rental.",
     "Heavy equipment and managed projects", "PHASE_1", "LICENCE_REQUIRED",
     "Hourly, daily, weekly, monthly or project pricing; disclosed platform split.",
     "rental / industrial_projects", "Operations + Safety"),
    ("accreditation_admin_fee", "Accreditation administration fee", "VERIFICATION",
     "Administrative fee for documentary processing; payment never guarantees approval.",
     "Provider vehicle and equipment applications", "PHASE_1", "STANDARD",
     "Published fixed or tiered schedule with waiver/refund controls.",
     "accreditation", "Compliance Operations"),
    ("payment_admin_fee", "Payment administration fee", "PAYMENTS",
     "Disclosed administration fee around a regulated partner payment flow.",
     "Protected-payment eligible transactions", "PHASE_2", "PARTNER_REQUIRED",
     "Fixed or percentage fee; LiftHaul does not hold funds unless separately authorised.",
     "protected_payment / payment_gateway", "Finance + Legal"),
    ("sponsored_placement", "Sponsored placement", "ADVERTISING",
     "Clearly labelled paid visibility that never changes safety or eligibility ranking.",
     "Verified providers only", "PHASE_2", "STANDARD",
     "Time-boxed campaign or impression/click package.",
     "future module", "Commercial + Consumer Protection"),
    ("api_white_label", "API and white-label licence", "PLATFORM",
     "Contracted platform, API, integration or white-label access.",
     "Enterprise and channel partners", "PHASE_2", "STANDARD",
     "Licence, implementation, support and governed usage fees.",
     "api_platform / saas", "Enterprise Commercial"),
    ("insurance_referral", "Insurance referral income", "REFERRAL",
     "Compensated insurance referral only through a structure accepted by Philippine counsel and the Insurance Commission.",
     "Cargo or equipment protection journeys", "PHASE_3", "LICENCE_REQUIRED",
     "No commission or referral compensation until licensing/partner structure is verified.",
     "cargo_insurance / goods_protection", "Legal + Insurance"),
    ("financing_referral", "Financing referral income", "REFERRAL",
     "Referral to a duly licensed financing/lending partner without LiftHaul underwriting or lending.",
     "Provider vehicle/equipment financing", "PHASE_3", "PARTNER_REQUIRED",
     "Partner-paid referral only after legal, disclosure and consent review.",
     "future module", "Legal + Finance"),
    ("managed_project_fee", "Managed project coordination fee", "PROJECT",
     "Scoped coordination, survey, scheduling and evidence fee for complex delivery or industrial projects.",
     "Enterprise and industrial projects", "PHASE_2", "STANDARD",
     "Approved statement of work, milestones and change controls.",
     "industrial_projects / billing", "Project Delivery"),
    ("data_analytics_addon", "Analytics and reporting add-on", "PLATFORM",
     "Paid reporting or analytics based only on authorised, minimised data.",
     "Enterprise subscribers", "PHASE_2", "STANDARD",
     "Subscription add-on or governed usage fee.",
     "reporting / saas", "Data Governance"),
]


BASE_CONTROLS = [
    ("business_registration", "Business registration and authority", "CORPORATE",
     "Current SEC/DTI/CDA registration and local permits appropriate to the contracting entity.", "Legal"),
    ("bir_registration_invoicing", "BIR registration, invoicing and tax treatment", "TAX",
     "BIR registration, invoice design, VAT/percentage-tax treatment and revenue recognition reviewed.", "Tax"),
    ("ita_disclosure_redress", "Internet Transactions Act disclosures and redress", "CONSUMER",
     "Platform identity, price/fee disclosures, terms, complaint/redress and merchant information reviewed.", "Legal + CX"),
    ("privacy_security", "Privacy, consent and security controls", "PRIVACY",
     "Lawful basis, notices, minimisation, retention, access controls, incident response and processor contracts verified.", "DPO + Security"),
    ("accounting_audit", "Accounting, reconciliation and audit design", "FINANCE",
     "Immutable fee snapshot, ledger mapping, refund/adjustment handling and reconciliation owner verified.", "Finance"),
]


SPECIFIC_CONTROLS = {
    "provider_success_fee": [
        ("marketplace_withholding", "Marketplace withholding assessment", "TAX", "Determine and configure applicable BIR marketplace withholding/remittance obligations.", "Tax"),
        ("carrier_authority", "Carrier and vehicle eligibility gate", "TRANSPORT", "Only duly evidenced carriers, drivers and vehicles may receive eligible work.", "Compliance"),
    ],
    "customer_platform_fee": [
        ("fee_precontract_disclosure", "Pre-contract fee disclosure", "CONSUMER", "Customer sees the fee, basis, taxes and refund treatment before confirmation.", "CX + Legal"),
    ],
    "fleet_subscription": [
        ("plan_terms", "Approved subscription plan and cancellation terms", "COMMERCIAL", "Published plan version, inclusions, limits, renewal, cancellation and support terms approved.", "Commercial"),
    ],
    "enterprise_subscription": [
        ("contract_dpa_sla", "Signed contract, DPA and SLA", "CONTRACT", "Executed commercial terms, data-processing terms, service levels and authorised signatories evidenced.", "Legal + Sales"),
    ],
    "heavy_equipment_rental": [
        ("equipment_inspection", "Equipment inspection and roadworthiness", "SAFETY", "Applicable third-party inspection, registration, maintenance and roadworthiness evidence verified.", "Safety + Compliance"),
        ("operator_qualification", "Operator qualification and site safety", "SAFETY", "Operator qualifications, authorised equipment class, project/site safety plan and insurance verified.", "Safety + Operations"),
        ("carrier_authority", "Transport authority where applicable", "TRANSPORT", "LTFRB/LTO and project-specific authority classification completed; required evidence verified.", "Legal + Compliance"),
    ],
    "accreditation_admin_fee": [
        ("no_guaranteed_approval", "No guaranteed approval representation", "CONSUMER", "UI, invoice and terms state that payment covers processing and does not guarantee verification or activation.", "Legal + Compliance"),
        ("published_refund_policy", "Published waiver and refund policy", "CONSUMER", "Waiver authority and refund events are documented and system-controlled.", "Finance + Compliance"),
    ],
    "payment_admin_fee": [
        ("merchant_operating_role", "Approved merchant and marketplace operating role", "LEGAL", "Written Philippine counsel analysis determines whether LiftHaul is merchant of record, marketplace operator or booking intermediary and defines prohibited custody and escrow representations.", "Legal + Finance"),
        ("bsp_ops_status", "Current BSP OPS status verified", "REGULATORY", "Current primary-source BSP directory evidence for every payment-system operator in the proposed fund flow is independently verified.", "Legal + Compliance"),
        ("merchant_acquisition_authority", "Merchant-acquisition authority verified", "REGULATORY", "The selected acquirer/payment facilitator's authority and LiftHaul merchant-acquisition structure are documented and approved where applicable.", "Legal + Compliance"),
        ("bsp_partner", "Contracted BSP-regulated payment partner", "PARTNER", "Executed provider agreement identifies the regulated contracting entity, service scope, settlement account, support and termination route.", "Legal + Finance"),
        ("fund_flow", "Approved fund-flow and safeguarding design", "PAYMENTS", "Collection, conditional settlement, refund, dispute, chargeback, payout and safeguarding flows are mapped; LiftHaul has no undocumented custody.", "Finance + Legal"),
        ("marketplace_capability", "Marketplace and conditional-payout capability", "PARTNER", "Provider confirms in writing that sub-accounts, KYB, payout recipients, conditional or delayed settlement and marketplace use are permitted for LiftHaul's model.", "Finance + Partnerships"),
        ("channel_scope", "Approved channel and transaction-limit register", "PAYMENTS", "Enabled channels, currencies, transaction limits, settlement periods and fees are approved; unsupported channels remain hidden.", "Finance + Product"),
        ("channel_certification", "Per-channel sandbox and production certification", "TESTING", "QR Ph, GCash, Maya, cards, bank transfer and any OTC channel are individually certified with traceable provider test-run evidence before display.", "Engineering + Finance"),
        ("payment_webhooks", "Production webhook authentication and replay testing", "SECURITY", "Signed production payment, refund, dispute, chargeback, payout and reversal callbacks pass authentication, idempotency, delay, duplicate and replay tests.", "Security + Engineering"),
        ("refund_dispute_rules", "Refund, dispute, cancellation and chargeback rules", "OPERATIONS", "Approved rules cover partial/full refunds, no-show, cancellation, damage, non-confirmation, disputes, chargebacks, evidence deadlines and manual authority.", "Legal + Operations"),
        ("payout_controls", "Verified payees and maker-checker payout controls", "PAYOUT", "KYB/KYC, beneficial-owner, payout-account ownership, sanctions/fraud checks, segregation of duties and duplicate-payout prevention are tested.", "Finance + Compliance"),
        ("reconciliation", "Provider settlement reconciliation", "FINANCE", "Automated daily transaction-to-settlement reconciliation uses provider reports or API data, records exceptions and has a named resolution owner.", "Finance + Engineering"),
        ("payment_security", "Independent payment security assessment", "SECURITY", "Secrets, hosted checkout, encryption, least privilege, logging, dependency scanning and an external penetration test are evidenced with material findings closed.", "Security"),
        ("postgres_recovery", "PostgreSQL recovery and payment replay exercise", "RESILIENCE", "Backup restore, database failover, durable queues, delayed webhooks and provider-outage recovery are tested against PostgreSQL with no duplicate charge or payout.", "Engineering + Security"),
        ("kyc_aml_fraud", "KYC, KYB, AML and fraud operating procedure", "COMPLIANCE", "Provider and LiftHaul responsibilities, transaction monitoring, limits, escalation, suspicious-activity handling and account suspension are approved.", "Compliance + Operations"),
        ("payment_terms_privacy", "Payment terms, privacy and processor agreements", "LEGAL", "Terms, fee disclosure, cancellation, refund, dispute, privacy notices, consent, retention and provider data-processing agreements are approved and published.", "Legal + DPO"),
        ("payment_tax_invoicing", "Payment tax and invoicing treatment", "TAX", "BIR treatment, VAT or percentage tax, invoice responsibility, provider charges, the 10% administration fee and provider payout records are approved.", "Tax + Finance"),
        ("controlled_live_pilot", "Controlled low-value live pilot", "TESTING", "Named approvers authorize capped transactions, verified participants, monitored routes, rollback criteria, support coverage and incident logging.", "Executive + Operations"),
        ("real_money_evidence", "Real refund and provider-payout confirmation", "TESTING", "Controlled live evidence confirms an actual customer payment, refund and payout to a verified provider without duplicate or unexplained settlement.", "Finance + Engineering"),
        ("reconciliation_soak", "Seven-day production reconciliation validation", "TESTING", "At least seven consecutive production days reconcile completely or every exception is documented, resolved and independently accepted.", "Finance + Security"),
        ("go_live_signoff", "Cross-functional payment go-live sign-off", "GOVERNANCE", "Founder/operator, Legal, Finance, Security, Engineering and Operations approve the exact provider, channels, limits, runbook and rollback plan.", "Executive"),
    ],
    "sponsored_placement": [
        ("ad_labelling", "Clear advertising label", "CONSUMER", "Paid placement is prominently labelled and cannot override compliance, safety or eligibility ranking.", "Legal + Product"),
    ],
    "api_white_label": [
        ("api_contract", "API/white-label contract and acceptable use", "CONTRACT", "Licence scope, branding, SLA, data roles, security, support and termination controls approved.", "Legal + Product"),
        ("integration_security", "Integration security assessment", "SECURITY", "Scoped credentials, tenant isolation, rate limits, logging and revocation tested.", "Security"),
    ],
    "insurance_referral": [
        ("insurance_licence", "Insurance intermediary licensing opinion", "LICENSING", "Written Philippine legal/Insurance Commission basis confirms the permitted compensated activity.", "Legal"),
        ("licensed_insurance_partner", "Licensed insurance partner", "PARTNER", "Current Insurance Commission authority, contract, product approval and customer disclosures verified.", "Legal + Partnerships"),
    ],
    "financing_referral": [
        ("licensed_finance_partner", "Licensed financing/lending partner", "PARTNER", "Partner authority, contract and complaint route verified; LiftHaul does not underwrite or hold loan funds.", "Legal + Partnerships"),
        ("credit_data_consent", "Credit-data consent and purpose limitation", "PRIVACY", "Explicit notices/consent, minimised transfer and retention controls approved.", "DPO + Legal"),
    ],
    "managed_project_fee": [
        ("project_sow", "Signed project scope and change control", "CONTRACT", "Deliverables, exclusions, milestones, acceptance evidence, HSE responsibility and changes are signed.", "Delivery + Legal"),
    ],
    "data_analytics_addon": [
        ("analytics_data_rights", "Analytics data rights and minimisation", "PRIVACY", "Contract permits the analysis; personal/confidential data is minimised and outputs cannot expose another party.", "DPO + Data"),
        ("analytics_claim_review", "Claims and methodology review", "CONSUMER", "Metrics, assumptions, freshness and limitations are visible; no unsupported performance claims.", "Data + Legal"),
    ],
}


LEGAL_BASIS = {
    "ita": [{"authority": "DTI", "topic": "RA 11967 / ITA IRR", "url": DTI_ITA},
            {"authority": "DTI", "topic": "E-Commerce Philippine Trustmark guidance", "url": DTI_TRUSTMARK}],
    "tax": [{"authority": "BIR", "topic": "Marketplace withholding guidance (RMC 8-2024 / RR 16-2023)", "url": BIR_MARKETPLACE}],
    "payments": [{"authority": "BSP", "topic": "Payment systems and OPS/MAL framework", "url": BSP_PAYMENTS}],
    "transport": [{"authority": "LTFRB", "topic": "Transport authority and classification", "url": LTFRB}],
    "privacy": [{"authority": "NPC", "topic": "Data Privacy Act compliance", "url": NPC}],
    "heavy": [{"authority": "DOLE OSHC", "topic": "Construction heavy-equipment testing/accreditation", "url": DOLE_HEAVY}],
    "insurance": [{"authority": "Insurance Commission", "topic": "Compensated insurance-referral licensing opinion", "url": IC_REFERRAL}],
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _json(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _legal_for(code):
    result = list(LEGAL_BASIS["ita"] + LEGAL_BASIS["tax"] + LEGAL_BASIS["privacy"])
    if code in ("provider_success_fee", "heavy_equipment_rental"):
        result += LEGAL_BASIS["transport"]
    if code == "heavy_equipment_rental":
        result += LEGAL_BASIS["heavy"]
    if code == "payment_admin_fee":
        result += LEGAL_BASIS["payments"]
    if code == "insurance_referral":
        result += LEGAL_BASIS["insurance"]
    return result


def seed(conn):
    system = 0
    for row in SCHEMES:
        code, name, category, description, applicability, phase, level, pricing, module, owner = row
        prohibited = [
            "Do not claim government approval without current evidence.",
            "Do not claim LiftHaul holds or safeguards customer funds unless separately authorised.",
            "Do not guarantee provider accreditation, delivery outcome, insurance coverage, financing, or earnings.",
        ]
        conn.execute(
            "INSERT INTO revenue_schemes(code,name,category,description,applicability,launch_phase,"
            "regulatory_level,pricing_model,existing_module,owner_role,legal_basis,prohibited_claims,"
            "status,created_by,created_at,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
            "'ASSESSMENT',?,?,?,?) ON CONFLICT(code) DO NOTHING",
            (code, name, category, description, applicability, phase, level, pricing, module, owner,
             _json(_legal_for(code)), _json(prohibited), system, _now(), system, _now()))
        scheme = conn.execute("SELECT id FROM revenue_schemes WHERE code=?", (code,)).fetchone()
        controls = BASE_CONTROLS + SPECIFIC_CONTROLS.get(code, [])
        for ccode, title, cat, desc, cowner in controls:
            conn.execute(
                "INSERT INTO revenue_controls(scheme_id,code,title,category,description,required,owner_role,status) "
                "VALUES(?,?,?,?,?,1,?,'OPEN') ON CONFLICT(scheme_id,code) DO NOTHING",
                (scheme["id"], ccode, title, cat, desc, cowner))
    conn.commit()


def _scheme(conn, code):
    row = conn.execute("SELECT * FROM revenue_schemes WHERE code=?", (code,)).fetchone()
    if not row:
        raise core.NotFoundError(f"revenue scheme '{code}' not found")
    return dict(row)


def _decode_scheme(row):
    out = dict(row)
    for key in ("legal_basis", "prohibited_claims"):
        try:
            out[key] = json.loads(out.get(key) or "[]")
        except (TypeError, ValueError):
            out[key] = []
    return out


def readiness(conn, code):
    scheme = _scheme(conn, code)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM revenue_controls WHERE scheme_id=? ORDER BY category,code", (scheme["id"],)).fetchall()]
    expired = []
    missing = []
    for control in rows:
        if control["status"] == "VERIFIED" and control.get("evidence_id"):
            ev = conn.execute("SELECT status,expires_at FROM revenue_evidence WHERE id=?", (control["evidence_id"],)).fetchone()
            if not ev or ev["status"] != "VERIFIED" or (ev["expires_at"] and ev["expires_at"] < _today()):
                expired.append(control["code"])
        if control["required"] and control["status"] not in ("VERIFIED", "NOT_APPLICABLE"):
            missing.append(control["code"])
    missing += [x for x in expired if x not in missing]
    verified = sum(1 for c in rows if c["status"] in ("VERIFIED", "NOT_APPLICABLE") and c["code"] not in expired)
    return {
        "ready": not missing,
        "scheme_code": code,
        "status": scheme["status"],
        "required_controls": sum(1 for c in rows if c["required"]),
        "verified_controls": verified,
        "missing_controls": missing,
        "expired_controls": expired,
    }


def list_schemes(conn, actor):
    core.require(actor, "revenue.dna.view")
    result = []
    for row in conn.execute("SELECT * FROM revenue_schemes ORDER BY launch_phase,category,name").fetchall():
        item = _decode_scheme(row)
        item["readiness"] = readiness(conn, item["code"])
        result.append(item)
    return result


def get_scheme(conn, actor, code):
    core.require(actor, "revenue.dna.view")
    scheme = _decode_scheme(_scheme(conn, code))
    scheme["controls"] = [dict(r) for r in conn.execute(
        "SELECT * FROM revenue_controls WHERE scheme_id=? ORDER BY category,code", (scheme["id"],)).fetchall()]
    scheme["evidence"] = [dict(r) for r in conn.execute(
        "SELECT * FROM revenue_evidence WHERE scheme_id=? ORDER BY id DESC", (scheme["id"],)).fetchall()]
    scheme["history"] = [dict(r) for r in conn.execute(
        "SELECT * FROM revenue_activation_log WHERE scheme_id=? ORDER BY id DESC", (scheme["id"],)).fetchall()]
    scheme["readiness"] = readiness(conn, code)
    return scheme


def summary(conn, actor):
    core.require(actor, "revenue.dna.view")
    schemes = list_schemes(conn, actor)
    by_status = {}
    by_phase = {}
    for s in schemes:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
        by_phase[s["launch_phase"]] = by_phase.get(s["launch_phase"], 0) + 1
    return {
        "total_schemes": len(schemes),
        "active_schemes": by_status.get("ACTIVE", 0),
        "ready_for_approval": sum(1 for s in schemes if s["readiness"]["ready"] and s["status"] == "ASSESSMENT"),
        "blocked_schemes": sum(1 for s in schemes if not s["readiness"]["ready"]),
        "by_status": by_status,
        "by_phase": by_phase,
        "enforcement_mode": enforcement_mode(),
    }


def update_scheme(conn, actor, code, **changes):
    core.require(actor, "revenue.dna.manage")
    scheme = _scheme(conn, code)
    if scheme["status"] in ("ACTIVE", "RETIRED"):
        raise core.ConflictError("active or retired revenue schemes cannot be edited; suspend or version first")
    allowed = ("applicability", "launch_phase", "pricing_model", "owner_role", "notes")
    updates = {k: v for k, v in changes.items() if k in allowed}
    if not updates:
        raise core.ValidationError("no permitted revenue-scheme fields supplied")
    assignments = ",".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE revenue_schemes SET {assignments},version=version+1,updated_by=?,updated_at=? WHERE id=?",
                 tuple(updates.values()) + ((actor or {}).get("id"), _now(), scheme["id"]))
    core.audit(conn, actor, "REVENUE_SCHEME_UPDATED", "revenue_schemes", scheme["id"], old=scheme, new=updates)
    conn.commit()
    return get_scheme(conn, actor, code)


def submit_evidence(conn, actor, code, control_code, evidence_ref, *, description=None,
                    issued_by=None, issued_at=None, expires_at=None):
    core.require(actor, "revenue.dna.evidence.manage")
    if not str(evidence_ref or "").strip():
        raise core.ValidationError("evidence_ref is required; upload/secret content must remain in the governed repository")
    scheme = _scheme(conn, code)
    control = conn.execute("SELECT * FROM revenue_controls WHERE scheme_id=? AND code=?",
                           (scheme["id"], control_code)).fetchone()
    if not control:
        raise core.NotFoundError(f"control '{control_code}' not found for '{code}'")
    if expires_at and expires_at < _today():
        raise core.ValidationError("expired evidence cannot be submitted as current")
    cur = conn.execute(
        "INSERT INTO revenue_evidence(scheme_id,control_id,evidence_ref,description,issued_by,issued_at,"
        "expires_at,status,submitted_by,submitted_at) VALUES(?,?,?,?,?,?,?,'SUBMITTED',?,?)",
        (scheme["id"], control["id"], str(evidence_ref).strip(), description, issued_by, issued_at,
         expires_at, (actor or {}).get("id"), _now()))
    conn.execute("UPDATE revenue_controls SET status='IN_REVIEW',evidence_id=?,verified_by=NULL,verified_at=NULL WHERE id=?",
                 (cur.lastrowid, control["id"]))
    core.audit(conn, actor, "REVENUE_EVIDENCE_SUBMITTED", "revenue_evidence", cur.lastrowid,
               new={"scheme": code, "control": control_code, "evidence_ref": str(evidence_ref).strip()})
    conn.commit()
    return {"evidence_id": cur.lastrowid, "status": "SUBMITTED"}


def verify_evidence(conn, actor, evidence_id, decision, reason=None):
    core.require(actor, "revenue.dna.evidence.verify")
    decision = str(decision or "").upper()
    if decision not in ("VERIFIED", "REJECTED"):
        raise core.ValidationError("decision must be VERIFIED or REJECTED")
    ev = conn.execute("SELECT * FROM revenue_evidence WHERE id=?", (evidence_id,)).fetchone()
    if not ev:
        raise core.NotFoundError(f"revenue evidence {evidence_id} not found")
    if ev["status"] != "SUBMITTED":
        raise core.ConflictError("only submitted evidence may be verified")
    if ev["submitted_by"] == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: evidence verifier must differ from submitter")
    if decision == "VERIFIED" and ev["expires_at"] and ev["expires_at"] < _today():
        raise core.ValidationError("expired evidence cannot be verified")
    conn.execute("UPDATE revenue_evidence SET status=?,verified_by=?,verified_at=?,rejection_reason=? WHERE id=?",
                 (decision, (actor or {}).get("id"), _now(), reason if decision == "REJECTED" else None, evidence_id))
    conn.execute("UPDATE revenue_controls SET status=?,verified_by=?,verified_at=?,notes=? WHERE id=?",
                 (decision, (actor or {}).get("id"), _now(), reason, ev["control_id"]))
    core.audit(conn, actor, "REVENUE_EVIDENCE_" + decision, "revenue_evidence", evidence_id,
               new={"decision": decision, "reason": reason})
    conn.commit()
    return {"evidence_id": evidence_id, "status": decision}


def _transition(conn, actor, scheme, action, to_status, reason=None, snapshot=None):
    old = scheme["status"]
    fields = {"SUBMIT": ("submitted_by", "submitted_at"), "APPROVE": ("approved_by", "approved_at"),
              "ACTIVATE": ("activated_by", "activated_at"), "SUSPEND": ("suspended_by", "suspended_at")}
    actor_field, time_field = fields[action]
    conn.execute(f"UPDATE revenue_schemes SET status=?,{actor_field}=?,{time_field}=?,updated_by=?,updated_at=? WHERE id=?",
                 (to_status, (actor or {}).get("id"), _now(), (actor or {}).get("id"), _now(), scheme["id"]))
    conn.execute("INSERT INTO revenue_activation_log(scheme_id,action,from_status,to_status,actor_id,reason,readiness_snapshot,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?)",
                 (scheme["id"], action, old, to_status, (actor or {}).get("id"), reason,
                  _json(snapshot) if snapshot is not None else None, _now()))
    core.audit(conn, actor, "REVENUE_" + action, "revenue_schemes", scheme["id"],
               old={"status": old}, new={"status": to_status}, reason=reason)
    conn.commit()


def submit_for_approval(conn, actor, code, reason=None):
    core.require(actor, "revenue.dna.submit")
    scheme = _scheme(conn, code)
    if scheme["status"] not in ("ASSESSMENT", "BLOCKED", "SUSPENDED"):
        raise core.ConflictError("scheme is not in a submittable state")
    check = readiness(conn, code)
    if not check["ready"]:
        raise core.ForbiddenError("revenue activation is fail-closed; unresolved controls: " + ", ".join(check["missing_controls"]))
    _transition(conn, actor, scheme, "SUBMIT", "READY_FOR_APPROVAL", reason, check)
    return get_scheme(conn, actor, code)


def approve(conn, actor, code, reason=None):
    core.require(actor, "revenue.dna.approve")
    scheme = _scheme(conn, code)
    if scheme["status"] != "READY_FOR_APPROVAL":
        raise core.ConflictError("scheme must be READY_FOR_APPROVAL")
    if scheme["submitted_by"] == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: approver must differ from submitter")
    check = readiness(conn, code)
    if not check["ready"]:
        raise core.ForbiddenError("evidence changed or expired after submission")
    _transition(conn, actor, scheme, "APPROVE", "APPROVED", reason, check)
    return get_scheme(conn, actor, code)


def activate(conn, actor, code, reason=None):
    core.require(actor, "revenue.dna.activate")
    scheme = _scheme(conn, code)
    if scheme["status"] != "APPROVED":
        raise core.ConflictError("scheme must be APPROVED")
    if scheme["approved_by"] == (actor or {}).get("id"):
        raise core.ForbiddenError("separation of duties: activator must differ from approver")
    check = readiness(conn, code)
    if not check["ready"]:
        raise core.ForbiddenError("revenue activation is fail-closed because evidence is incomplete or expired")
    _transition(conn, actor, scheme, "ACTIVATE", "ACTIVE", reason, check)
    return get_scheme(conn, actor, code)


def suspend(conn, actor, code, reason):
    core.require(actor, "revenue.dna.suspend")
    scheme = _scheme(conn, code)
    if scheme["status"] != "ACTIVE":
        raise core.ConflictError("only an ACTIVE scheme may be suspended")
    if not str(reason or "").strip():
        raise core.ValidationError("suspension reason is required")
    _transition(conn, actor, scheme, "SUSPEND", "SUSPENDED", str(reason).strip(), readiness(conn, code))
    return get_scheme(conn, actor, code)


def enforcement_mode():
    mode = str(os.environ.get("REVENUE_DNA_ENFORCEMENT", "observe")).strip().lower()
    return mode if mode in ("observe", "enforce") else "enforce"


def guard(conn, actor, code, *, operation=None):
    """Check a runtime earning event against the DNA registry.

    ``observe`` supports migration without silently claiming readiness.  The return
    explicitly says blocked, and an audit event is created.  ``enforce`` raises and
    therefore prevents the earning transaction.  Production should use ``enforce``.
    """
    scheme = _scheme(conn, code)
    check = readiness(conn, code)
    allowed = scheme["status"] == "ACTIVE" and check["ready"]
    outcome = {"allowed": allowed, "scheme_code": code, "scheme_status": scheme["status"],
               "operation": operation, "enforcement_mode": enforcement_mode(), "readiness": check}
    core.audit(conn, actor, "REVENUE_GUARD_ALLOWED" if allowed else "REVENUE_GUARD_BLOCKED",
               "revenue_schemes", scheme["id"], new=outcome)
    # The guard is intentionally called before the earning transaction begins. Persist the
    # governance decision independently so a rejected or later-failed transaction cannot erase it.
    conn.commit()
    if not allowed and enforcement_mode() == "enforce":
        raise core.ForbiddenError(
            f"revenue DNA blocked '{operation or code}': scheme {code} is {scheme['status']} "
            f"with unresolved controls {check['missing_controls']}")
    return outcome


def payment_dna_readiness(conn):
    """Return the single authoritative, secret-free live-money decision.

    Provider credentials, feature flags and certified channel rows are necessary but
    never sufficient.  The payment scheme itself must be ACTIVE and every required
    evidence control must still be current.
    """
    scheme = _scheme(conn, PAYMENT_DNA_SCHEME)
    check = readiness(conn, PAYMENT_DNA_SCHEME)
    allowed = scheme["status"] == "ACTIVE" and check["ready"]
    return {
        "scheme_code": PAYMENT_DNA_SCHEME,
        "scheme_status": scheme["status"],
        "evidence_ready": check["ready"],
        "live_money_allowed": allowed,
        "required_controls": check["required_controls"],
        "verified_controls": check["verified_controls"],
        "missing_controls": check["missing_controls"],
        "expired_controls": check["expired_controls"],
    }


def guard_payment_operation(conn, actor, operation, *, moving_real_funds=True,
                            risk_reducing_unwind=False):
    """Fail closed for every real-money edge, regardless of migration mode.

    ``REVENUE_DNA_ENFORCEMENT=observe`` may be used while non-money revenue paths are
    being inventoried. It can never authorize collection, refund, transfer or payout.
    This makes direct service-function calls subject to the same DNA as HTTP routes.
    """
    report = payment_dna_readiness(conn)
    operation_allowed = report["live_money_allowed"] or bool(risk_reducing_unwind)
    outcome = {**report, "operation": operation, "moving_real_funds": bool(moving_real_funds),
               "risk_reducing_unwind": bool(risk_reducing_unwind),
               "operation_allowed": operation_allowed}
    scheme = _scheme(conn, PAYMENT_DNA_SCHEME)
    action = ("PAYMENT_DNA_UNWIND_ALLOWED" if risk_reducing_unwind else
              ("PAYMENT_DNA_ALLOWED" if operation_allowed else "PAYMENT_DNA_BLOCKED"))
    core.audit(conn, actor, action,
               "revenue_schemes", scheme["id"], new=outcome)
    conn.commit()
    if moving_real_funds and not operation_allowed:
        raise core.ForbiddenError(
            f"payment DNA blocked '{operation}': scheme is {report['scheme_status']} with "
            f"unresolved controls {report['missing_controls']}")
    return outcome
