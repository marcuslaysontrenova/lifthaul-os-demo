"""LiftHaul OS — Enterprise Administration Platform (Platform 1) foundation.

This is the control layer that governs the operational modules — it does NOT replace
them. It implements the three keystone capabilities from the Enterprise Product
Blueprint (docs/blueprint), on which the rest of Platform 1 depends:

  * C-003  Tenant / Organization dimension  (LiftHaul OS = product, RGO = tenant 0)
  * C-005  Data-driven RBAC                  (roles/permissions as DATA, not core.PERMISSIONS)
  * C-008  Configuration cascade + registry  (platform -> tenant -> unit -> user)

Design rules honored:
  - ADDITIVE: the 96-test commercial spine keeps working; this sits above it. The
    seed mirrors today's core.PERMISSIONS so behavior parity is provable before any cutover.
  - CONFIGURATION-FIRST (ED-004): roles, permissions, and limits become admin-owned data.
  - MULTI-TENANT: roles/config are tenant-scoped; system roles are global templates.
  - GOVERNED: mutations emit audit_logs events via core.audit.

Role model reflects the CTO 4-layer administration hierarchy:
  Layer 1 Platform Administration   (Super Platform Admin, Platform Admin)
  Layer 2 Company Administration    (Business Administrator)
  Layer 3 Functional Administration (CRM / Fleet / Finance / Dispatch / Safety Admin)
  Layer 4 Operational Users         (Ops Mgr, Estimator, Approver, Finance, Dispatcher,
                                     Fleet Mgr, Safety Officer, Mechanic, Driver, Operator, Customer)
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import re
import secrets
import struct
import time

import core   # for audit() + shared error types; core does not import this module


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, legal_name TEXT NOT NULL,
  trading_name TEXT, status TEXT DEFAULT 'ACTIVE', plan TEXT DEFAULT 'STANDARD',
  created_at TEXT);

CREATE TABLE IF NOT EXISTS admin_permissions(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, module TEXT NOT NULL,
  action TEXT NOT NULL, description TEXT);

CREATE TABLE IF NOT EXISTS admin_roles(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL,
  name TEXT NOT NULL, layer INTEGER NOT NULL, system_locked INTEGER DEFAULT 0,
  created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS admin_role_permissions(
  id INTEGER PRIMARY KEY, role_id INTEGER NOT NULL REFERENCES admin_roles(id),
  permission_code TEXT NOT NULL, UNIQUE(role_id, permission_code));

CREATE TABLE IF NOT EXISTS admin_user_roles(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, role_id INTEGER NOT NULL REFERENCES admin_roles(id),
  UNIQUE(user_id, role_id));

CREATE TABLE IF NOT EXISTS platform_config(
  id INTEGER PRIMARY KEY, scope TEXT NOT NULL, scope_ref TEXT NOT NULL DEFAULT '',
  key TEXT NOT NULL, value TEXT, effective_to TEXT, updated_by INTEGER, updated_at TEXT,
  UNIQUE(scope, scope_ref, key));

CREATE TABLE IF NOT EXISTS login_history(
  id INTEGER PRIMARY KEY, user_id INTEGER, email TEXT, success INTEGER NOT NULL,
  reason TEXT, ip TEXT, ts TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS mfa_enrollments(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, secret TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0, enrolled_at TEXT, UNIQUE(user_id));

CREATE TABLE IF NOT EXISTS cross_access_grants(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, source_tenant INTEGER, target_tenant TEXT,
  reason TEXT, correlation_id TEXT, activated_at TEXT, expires_at TEXT, terminated_at TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE');
"""

# tenant_id 0 is reserved to mean "platform-global" (system role templates, platform config).
PLATFORM_TENANT = 0


def init(conn):
    conn.executescript(SCHEMA)
    import config_registry; config_registry.init(conn)   # definitions table exists before any set_config
    _ensure_columns(conn, "users", {"status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
                                    "last_login_at": "TEXT", "tenant_id": "INTEGER"})
    _ensure_columns(conn, "sessions", {"ip": "TEXT", "last_seen": "TEXT"})
    _ensure_columns(conn, "platform_config", {"effective_to": "TEXT"})
    _ensure_columns(conn, "audit_logs", {"correlation_id": "TEXT"})
    _ensure_columns(conn, "quotations", {"tax_snapshot": "TEXT", "dp_snapshot": "TEXT", "approval_snapshot": "TEXT"})
    _ensure_columns(conn, "payment_requests", {"dp_snapshot": "TEXT"})
    conn.commit()


def _ensure_columns(conn, table, cols_spec):
    """Idempotent additive migration for pre-existing SQLite DBs (C-006/C-007).
    Fresh DBs get columns from core.SCHEMA; Postgres from the translated DDL."""
    try:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return                                             # non-SQLite: columns come from DDL
    if not have:
        return
    for col, spec in cols_spec.items():
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {spec}")
    conn.commit()


# --------------------------------------------------------------------------- #
# Permission catalog  (module x action)  — seeded from today's require() sites,
# expanded with the CTO's full quotation action set and the admin modules.
# --------------------------------------------------------------------------- #
_ACTIONS_CRUD = ("view", "create", "edit", "delete", "export")
CATALOG = []

def _perm(module, action, desc=""):
    CATALOG.append((f"{module}.{action}", module, action, desc or f"{action} {module}"))

# Commercial / operational (mirror existing capabilities)
for _m in ("customer", "booking", "job"):
    for _a in _ACTIONS_CRUD:
        _perm(_m, _a)
for _a in ("view", "create", "edit", "delete", "approve", "archive", "restore",
           "export", "print", "email", "generate_pdf", "submit", "revise"):
    _perm("quotation", _a)                       # CTO: each independently assignable
for _a in ("view", "review", "ready", "create", "read"):
    _perm("booking", _a)
for _a in ("read", "create", "link", "verify", "refund", "view"):
    _perm("payment", _a)
for _a in ("read", "dispatch", "transition", "safety", "reserve", "view"):
    _perm("job", _a)
for _a in ("view", "manage", "reserve"):
    _perm("fleet", _a)
for _a in ("view", "record", "manage"):
    _perm("safety", _a)
for _a in ("view", "post", "approve", "export"):
    _perm("finance", _a)
BOOKING_QUOTATION_PERMISSIONS = [
    "booking.edit", "booking.edit_operational", "booking.edit_commercial",
    "booking.edit_draft", "booking.revise_returned", "booking.submit", "booking.cancel",
    "booking.delete_draft", "booking.attachment.manage", "booking.print", "booking.audit.view",
    "quotation.edit_draft", "quotation.revise_returned", "quotation.cancel",
    "quotation.delete_draft", "quotation.reject", "quotation.return",
    "quotation.customer_price.view", "quotation.carrier_cost.view",
    "quotation.platform_fee.view", "quotation.margin.view", "quotation.audit.view",
    "quotation.approve.exceptional", "user_admin.assign_roles", "role_admin.assign_privileged",
    # Quotation pricing subsystem — field-level edit + governed override controls:
    "quotation.customer_price.edit", "quotation.carrier_cost.edit",
    "quotation.rate.override", "quotation.discount.override",
]
for _code in BOOKING_QUOTATION_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Administration modules (Platform 1)
for _mod in ("tenant", "license", "org", "user_admin", "role_admin", "permission_admin",
             "workflow_admin", "crm_admin", "fleet_admin", "finance_admin", "dispatch_admin",
             "master_data", "system_config", "branding", "integration", "ai_admin",
             "reporting", "audit", "security"):
    for _a in ("view", "manage"):
        _perm(_mod, _a)
# Granular Phase-2 configuration permissions (multi-dot codes; added verbatim)
CONFIG_PERMISSIONS = [
    "admin.configuration.view", "admin.configuration.definition.manage", "admin.configuration.value.manage",
    "admin.configuration.override", "admin.configuration.simulate", "admin.configuration.history.view",
    "admin.configuration.financial.manage", "admin.configuration.security.manage",
    "quotation.policy.approval.view", "quotation.policy.approval.manage", "quotation.policy.approval.simulate",
    "tax.policy.view", "tax.policy.manage", "tax.policy.simulate",
    "payment.downpayment.policy.view", "payment.downpayment.policy.manage", "payment.downpayment.policy.simulate",
]
for _code in CONFIG_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-3 CRM administration + master-data permissions (multi-dot codes)
PHASE3_PERMISSIONS = [
    "crm.admin.classification.view", "crm.admin.classification.manage",
    "crm.admin.credit_policy.view", "crm.admin.credit_policy.manage",
    "crm.admin.pricing.view", "crm.admin.pricing.manage",
    "crm.admin.duplicate_rule.view", "crm.admin.duplicate_rule.manage",
    "crm.admin.merge.execute", "crm.admin.numbering.manage",
    "crm.admin.custom_field.view", "crm.admin.custom_field.manage",
    "master_data.archive", "master_data.restore", "master_data.import",
    "master_data.export", "master_data.replace", "master_data.system.manage",
]
for _code in PHASE3_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-4 workflow administration permissions (multi-dot codes)
PHASE4_PERMISSIONS = [
    "workflow.definition.view", "workflow.definition.manage",
    "workflow.version.create", "workflow.version.validate", "workflow.version.approve",
    "workflow.version.publish", "workflow.version.retire", "workflow.simulate",
    "workflow.instance.view", "workflow.instance.manage", "workflow.instance.reassign",
    "workflow.instance.cancel", "workflow.approval.execute", "workflow.approval.delegate",
    "workflow.sla.manage", "workflow.escalation.manage",
]
for _code in PHASE4_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-5 form & custom-field administration permissions (multi-dot codes)
PHASE5_PERMISSIONS = [
    "form.definition.view", "form.definition.manage",
    "form.version.create", "form.version.validate", "form.version.approve",
    "form.version.publish", "form.version.retire", "form.simulate",
    "form.layout.view", "form.layout.manage",
    "form.field.view", "form.field.manage", "form.field.sensitive.manage",
    "form.field.option.manage", "form.field.validation.manage", "form.field.visibility.manage",
    "form.data.view", "form.data.edit", "form.data.export",
    "form.data.sensitive.view", "form.data.sensitive.edit", "form.data.remediate",
]
for _code in PHASE5_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-6 platform & system settings permissions (multi-dot codes)
PHASE6_PERMISSIONS = [
    "platform.settings.view", "platform.settings.manage",
    "tenant.settings.view", "tenant.settings.manage", "organization.settings.override",
    "security.policy.view", "security.policy.manage",
    "authentication.policy.manage", "session.policy.manage",
    "numbering.view", "numbering.manage", "currency.settings.manage", "fiscal.settings.manage",
    "calendar.settings.manage", "branding.view", "branding.manage",
    "template.view", "template.manage", "template.publish",
    "retention.view", "retention.manage", "audit.retention.manage",
    "file.policy.view", "file.policy.manage", "api.policy.view", "api.policy.manage",
    "feature_flag.view", "feature_flag.manage", "feature_flag.emergency_disable",
    "module.view", "module.manage",
    "maintenance.view", "maintenance.manage", "maintenance.platform_manage",
    "backup.view", "backup.manage", "backup.execute", "restore.execute", "restore.approve",
]
for _code in PHASE6_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-7 integration administration + Wise payments permissions (multi-dot codes)
PHASE7_PERMISSIONS = [
    "integration.catalog.view", "integration.profile.view", "integration.profile.manage",
    "integration.secret.view_metadata", "integration.secret.manage",
    "integration.health.view", "integration.health.test",
    "integration.webhook.view", "integration.webhook.manage", "integration.webhook.replay",
    "integration.polling.view", "integration.polling.manage",
    "integration.dead_letter.view", "integration.dead_letter.manage", "integration.replay.execute",
    "payment.wise.view", "payment.wise.manage", "payment.wise.quote.create",
    "payment.wise.transfer.create", "payment.wise.transfer.cancel", "payment.wise.reconcile",
    "payment.wise.verify", "payment.wise.refund.request", "payment.wise.refund.approve",
    "integration.email.manage", "integration.sms.manage", "integration.maps.manage",
    "integration.accounting.manage", "integration.fx.manage",
]
for _code in PHASE7_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-8 reporting & dashboard administration permissions (multi-dot codes)
PHASE8_PERMISSIONS = [
    "report.datasource.view", "report.datasource.manage",
    "report.definition.view", "report.definition.manage",
    "report.version.create", "report.version.validate", "report.version.approve",
    "report.version.publish", "report.version.retire",
    "report.execute", "report.export", "report.schedule", "report.history.view",
    "report.sensitive.view", "report.sensitive.export",
    "dashboard.view", "dashboard.manage", "dashboard.publish",
    "kpi.view", "kpi.manage",
    "report.security.manage", "report.cache.manage", "report.platform.cross_tenant",
]
for _code in PHASE8_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-9 AI administration permissions (multi-dot codes)
PHASE9_PERMISSIONS = [
    "ai.use_case.view", "ai.use_case.manage", "ai.model.view", "ai.model.manage", "ai.model.approve",
    "ai.prompt.view", "ai.prompt.manage", "ai.prompt.validate", "ai.prompt.approve", "ai.prompt.publish",
    "ai.prompt.retire", "ai.simulate", "ai.execute", "ai.review", "ai.review.override",
    "ai.sensitive.execute", "ai.usage.view", "ai.cost.view", "ai.budget.manage", "ai.audit.view",
    "ai.incident.view", "ai.incident.manage", "ai.kill_switch.manage", "ai.platform.manage",
]
for _code in PHASE9_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))
# Granular Phase-10 SaaS commercial permissions (multi-dot codes)
PHASE10_PERMISSIONS = [
    "saas.product.view", "saas.product.manage", "saas.plan.view", "saas.plan.manage",
    "saas.plan.approve", "saas.plan.publish", "saas.subscription.view", "saas.subscription.manage",
    "saas.subscription.activate", "saas.subscription.suspend", "saas.subscription.reactivate",
    "saas.subscription.terminate", "saas.entitlement.view", "saas.entitlement.manage",
    "saas.entitlement.override", "saas.usage.view", "saas.usage.manage", "saas.quota.view",
    "saas.quota.manage", "saas.billing.view", "saas.billing.generate", "saas.billing.approve",
    "saas.pricing.view", "saas.pricing.manage", "saas.discount.manage",
    "saas.tenant.provision", "saas.tenant.activate", "saas.tenant.decommission",
]
for _code in PHASE10_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))

# Marketplace foundation (Nationwide Marketplace program, increment 1): governed cargo/vehicle
# taxonomy + lane coverage. lane.activate is deliberately separate from lane.manage so activation
# (the "promise service" gate) can be held under separation of duties from lane assessment.
MARKETPLACE_PERMISSIONS = [
    "marketplace.vehicle.view", "marketplace.vehicle.manage",
    "marketplace.cargo.view", "marketplace.cargo.manage",
    "marketplace.lane.view", "marketplace.lane.manage", "marketplace.lane.activate", "marketplace.lane.assess",
    # Increment 2: participant onboarding + compliance + eligibility (verify/activate held separately for SoD)
    "marketplace.shipper.application.view", "marketplace.shipper.application.manage",
    "marketplace.shipper.verify", "marketplace.shipper.activate", "marketplace.shipper.suspend",
    "marketplace.carrier.application.view", "marketplace.carrier.application.manage",
    "marketplace.carrier.verify", "marketplace.carrier.activate", "marketplace.carrier.suspend",
    "marketplace.vehicle.verify", "marketplace.vehicle.activate", "marketplace.vehicle.suspend",
    "marketplace.driver.view", "marketplace.driver.manage", "marketplace.driver.verify",
    "marketplace.driver.activate", "marketplace.driver.suspend",
    "marketplace.compliance.view", "marketplace.compliance.manage", "marketplace.compliance.verify",
    "marketplace.compliance.override", "marketplace.eligibility.test",
    # Trust / KYB / fraud / trust-score layer:
    "marketplace.kyb.view", "marketplace.kyb.manage",
    "marketplace.fraud.view", "marketplace.fraud.manage", "marketplace.trust.view",
    # Trust closure: driver qualification / vehicle legality / payout security / disputes / claims:
    "marketplace.driver.qualify", "marketplace.vehicle.legality",
    "marketplace.payout.manage", "marketplace.payout.approve",
    "marketplace.dispute.manage", "marketplace.claim.manage", "marketplace.claim.view",
    # Increment 3: booking / pricing / matching / broadcast / offers / bidding / assignment
    "marketplace.booking.view", "marketplace.booking.create", "marketplace.booking.manage",
    "marketplace.booking.validate", "marketplace.booking.cancel",
    "marketplace.pricing.view", "marketplace.pricing.manage", "marketplace.pricing.simulate",
    "marketplace.pricing.override", "marketplace.pricing.approve",
    "marketplace.matching.view", "marketplace.matching.execute", "marketplace.ranking.manage",
    "marketplace.broadcast.view", "marketplace.broadcast.manage", "marketplace.broadcast.execute",
    "marketplace.offer.view", "marketplace.offer.create", "marketplace.offer.manage",
    "marketplace.offer.evaluate", "marketplace.offer.select",
    "marketplace.bid.view", "marketplace.bid.manage", "marketplace.bid.participate",
    "marketplace.assignment.view", "marketplace.assignment.create", "marketplace.assignment.approve",
    "marketplace.assignment.confirm", "marketplace.assignment.cancel", "marketplace.assignment.reassign",
    # Increment 4: protected payment / release / payout / refund / dispute / freeze / reconciliation
    "marketplace.payment.view", "marketplace.payment.create", "marketplace.payment.reconcile",
    "marketplace.payment.verify", "marketplace.payment.override", "marketplace.payment.manage",
    "marketplace.release.view", "marketplace.release.evaluate", "marketplace.release.create",
    "marketplace.release.approve", "marketplace.release.submit",
    "marketplace.payout.view", "marketplace.payout.manage", "marketplace.payout.approve",
    "marketplace.refund.view", "marketplace.refund.request", "marketplace.refund.approve", "marketplace.refund.submit",
    "marketplace.dispute.view", "marketplace.dispute.create", "marketplace.dispute.manage",
    "marketplace.dispute.resolve", "marketplace.dispute.override",
    "marketplace.freeze.view", "marketplace.freeze.create", "marketplace.freeze.approve", "marketplace.freeze.release",
    "marketplace.reconciliation.view", "marketplace.reconciliation.manage",
    "marketplace.finance.integrity.view", "marketplace.finance.integrity.manage",
    # Increment 5: trip execution / GPS / geofence / proof-of-delivery / exceptions
    "marketplace.trip.view", "marketplace.trip.manage", "marketplace.trip.activate", "marketplace.trip.execute",
    "marketplace.gps.ingest", "marketplace.geofence.manage",
    "marketplace.pod.submit", "marketplace.pod.verify", "marketplace.exception.manage",
    # Regulatory closure: LTFRB carrier transport-authority (CPC) + BSP/provider readiness dashboard
    "marketplace.ltfrb.view", "marketplace.ltfrb.manage",
    # Cargo Insurance / Goods Protection (coverage orchestration; claims reuse marketplace.claim.*)
    "marketplace.insurance.view", "marketplace.insurance.manage",
    # Secure Delivery Verification / recipient OTP (driver may verify, never view/override the code)
    "delivery.verification.view", "delivery.verification.issue", "delivery.verification.resend",
    "delivery.verification.verify", "delivery.verification.override",
]
for _code in MARKETPLACE_PERMISSIONS:
    CATALOG.append((_code, _code.rsplit(".", 1)[0], _code.rsplit(".", 1)[1], _code))


# --------------------------------------------------------------------------- #
# System roles  (global templates, tenant_id = PLATFORM, system_locked = 1)
# grants use the same wildcard grammar as core.can: "*", "module.*", or exact code.
#
# IMPORTANT: the operational permission model is assembled across modules at import
# time — core.PERMISSIONS is the BASE and admin/ops/catalog/pdfgen augment it with
# `|=`. So we seed operational-role grants FROM the assembled core.PERMISSIONS (the
# single source of truth), guaranteeing parity by construction and auto-capturing any
# future permission a domain module contributes. Only the NEW admin-layer roles
# (Platform 1) carry explicit grants here.
# --------------------------------------------------------------------------- #

# New administration roles introduced by Platform 1 (not present in core.PERMISSIONS).
ADMIN_ROLES = [
    # code, display name, layer, grant patterns
    ("super_platform_admin", "Super Platform Administrator", 1, {"*"}),
    ("platform_admin",       "Platform Administrator",       1,
        {"tenant.*", "license.*", "integration.*", "system_config.*", "ai_admin.*",
         "workflow_admin.*", "branding.*", "security.*", "audit.*", "reporting.*",
         "master_data.*", "admin.configuration.*", "tax.policy.*",
         "quotation.policy.approval.*", "payment.downpayment.policy.*", "crm.admin.*",
         "workflow.*", "form.*",
         # Phase 6 platform & system settings (platform admin is the platform authority)
         "platform.settings.*", "tenant.settings.*", "organization.settings.override",
         "security.policy.*", "authentication.policy.manage", "session.policy.manage",
         "numbering.*", "currency.settings.manage", "fiscal.settings.manage", "calendar.settings.manage",
         "branding.*", "template.*", "retention.*", "audit.retention.manage",
         "file.policy.*", "api.policy.*", "feature_flag.*", "module.*", "maintenance.*",
         "backup.*", "restore.execute", "restore.approve",
         # Phase 7 integration administration + Wise (platform admin is the integration authority)
         "integration.catalog.view", "integration.profile.*", "integration.secret.*",
         "integration.health.*", "integration.webhook.*", "integration.polling.*",
         "integration.dead_letter.*", "integration.replay.execute", "payment.wise.*",
         "integration.email.manage", "integration.sms.manage", "integration.maps.manage",
         "integration.accounting.manage", "integration.fx.manage",
         # Phase 8 reporting & dashboard administration (platform admin is the reporting authority)
         "report.datasource.*", "report.definition.*", "report.version.*", "report.execute",
         "report.export", "report.schedule", "report.history.view", "report.sensitive.view",
         "report.sensitive.export", "dashboard.*", "kpi.*", "report.security.manage",
         "report.cache.manage", "report.platform.cross_tenant",
         # Phase 9 AI administration (platform admin is the AI governance authority)
         "ai.use_case.*", "ai.model.*", "ai.prompt.*", "ai.simulate", "ai.execute", "ai.review",
         "ai.review.override", "ai.sensitive.execute", "ai.usage.view", "ai.cost.view",
         "ai.budget.manage", "ai.audit.view", "ai.incident.*", "ai.kill_switch.manage", "ai.platform.manage",
         # Phase 10 SaaS commercial layer (platform admin is the commercial authority)
         "saas.product.*", "saas.plan.*", "saas.subscription.*", "saas.entitlement.*", "saas.usage.*",
         "saas.quota.*", "saas.billing.*", "saas.pricing.*", "saas.discount.manage",
         "saas.tenant.provision", "saas.tenant.activate", "saas.tenant.decommission",
         # Marketplace foundation (platform admin is the marketplace-catalog + lane authority)
         "marketplace.vehicle.*", "marketplace.cargo.*", "marketplace.lane.*"}),
    ("business_admin",       "Business Administrator",       2,
        {"org.*", "user_admin.*", "role_admin.*", "permission_admin.*", "crm_admin.*",
         "master_data.*", "crm.admin.*", "system_config.view", "reporting.*", "audit.view",
         "admin.configuration.view", "admin.configuration.value.manage",
         "admin.configuration.override", "admin.configuration.simulate",
         "admin.configuration.history.view",
         # workflow: design/validate/simulate/operate + SLA/escalation/delegation, but NOT publish/retire
         "workflow.definition.view", "workflow.definition.manage", "workflow.version.create",
         "workflow.version.validate", "workflow.simulate", "workflow.instance.view",
         "workflow.instance.manage", "workflow.instance.reassign", "workflow.approval.execute",
         "workflow.approval.delegate", "workflow.sla.manage", "workflow.escalation.manage",
         # forms: design/validate/simulate + field/layout/option/data, but NOT publish/retire or sensitive
         "form.definition.view", "form.definition.manage", "form.version.create",
         "form.version.validate", "form.simulate", "form.layout.view", "form.layout.manage",
         "form.field.view", "form.field.manage", "form.field.option.manage",
         "form.field.validation.manage", "form.field.visibility.manage",
         "form.data.view", "form.data.edit", "form.data.export"}),
    ("executive",            "Executive View",               2,
        {"booking.read", "booking.view", "booking.audit.view", "quotation.read", "quotation.view",
         "quotation.customer_price.view", "quotation.carrier_cost.view", "quotation.platform_fee.view",
         "quotation.margin.view", "quotation.audit.view", "quotation.approve.exceptional",
         "payment.read", "finance.view", "reporting.view", "audit.view", "job.read",
         "customer.read", "fleet.view", "safety.view"}),
    ("user_administrator",   "User Administrator",           3,
        {"user_admin.view", "user_admin.manage", "user_admin.assign_roles", "role_admin.view",
         "security.view", "org.view"}),
    ("booking_quotation_administrator", "Booking & Quotation Administrator", 3,
        {"customer.read", "booking.create", "booking.read", "booking.view", "booking.review",
         "booking.ready", "booking.edit", "booking.edit_operational", "booking.edit_commercial",
         "booking.edit_draft", "booking.revise_returned", "booking.submit",
         "booking.cancel", "booking.delete_draft", "booking.attachment.manage", "booking.export",
         "booking.print", "booking.audit.view", "quotation.create", "quotation.read", "quotation.view",
         "quotation.edit_draft", "quotation.revise", "quotation.revise_returned", "quotation.submit",
         "quotation.cancel", "quotation.delete_draft", "quotation.export", "quotation.print",
         "quotation.audit.view", "quotation.customer_price.view",
         "quotation.customer_price.edit", "quotation.rate.override", "quotation.discount.override"}),
    ("crm_admin",            "CRM Administrator",            3,
        {"crm_admin.*", "customer.*", "contact.*", "address.*", "crm.admin.*", "master_data.*"}),
    ("fleet_admin",          "Fleet Administrator",          3, {"fleet_admin.*", "equipment.*", "vehicle.*", "maintenance.*", "inspection.*"}),
    ("finance_admin",        "Finance Administrator",        3,
        {"finance_admin.*", "invoice.*", "expense.*", "payment.*", "refund.*",
         "booking.read", "booking.view", "quotation.read", "quotation.view",
         "quotation.customer_price.view", "quotation.carrier_cost.view", "quotation.carrier_cost.edit",
         "quotation.platform_fee.view", "quotation.margin.view", "quotation.audit.view",
         "tax.policy.*", "payment.downpayment.policy.*", "quotation.policy.approval.view",
         "admin.configuration.financial.manage", "admin.configuration.view", "admin.configuration.simulate",
         "crm.admin.credit_policy.*", "crm.admin.pricing.*"}),
    ("dispatch_admin",       "Dispatch Administrator",       3, {"dispatch_admin.*", "job.read", "job.dispatch", "job.transition", "reservation.*"}),
    ("safety_admin",         "Safety Administrator",         3, {"safety.*", "incident.*"}),
]

# Operational roles: (code -> display name, layer). Grants are pulled from the assembled
# core.PERMISSIONS at seed time; DEFAULT_GRANT is used only if a code isn't defined there.
OPERATIONAL_ROLE_META = {
    "super_admin":        ("Super Administrator", 1),
    "admin":              ("Administrator",       1),
    "owner":              ("Owner",               1),
    "operations_manager": ("Operations Manager",  4),
    "estimator":          ("Estimator",           4),
    "approver":           ("Approver",            4),
    "finance":            ("Finance Clerk",       4),
    "dispatcher":         ("Dispatcher",          4),
    "fleet_manager":      ("Fleet Manager",       4),
    "safety_officer":     ("Safety Officer",      4),
    "mechanic":           ("Mechanic",            4),
    "driver":             ("Driver",              4),
    "operator":           ("Operator",            4),
    "customer":           ("Customer",            4),
}
_DEFAULT_GRANT = {"job.read"}

# Default configuration seed — the constants ED-004 says become admin-owned data.
DEFAULT_CONFIG = {
    "approval.quotation_threshold": "500000",   # was hard-coded ₱500k in the approval path
    "finance.downpayment_pct": "30",            # was hard-coded 30%
    "finance.vat_pct": "12",                    # was hard-coded 12%
    "quotation.validity_days": "30",
    "dispatch.double_book": "block",            # reservation conflict policy
    "iam.rbac_source": "hybrid",                # legacy | hybrid | db  (C-005 cutover switch)
    # Authentication policy (C-007) — all admin-owned, resolved via the config cascade
    "auth.pw_min_length": "10",
    "auth.pw_require_complexity": "true",       # upper + lower + digit
    "auth.lockout_threshold": "5",              # consecutive failures before lock (0 = off)
    "auth.lockout_window_min": "15",            # minutes the failure window spans
    "auth.mfa_policy": "optional",              # off | optional | required
    # Phase 2 governed business policies — defaults EQUAL the pre-Phase-2 constants
    "quotation.approval.threshold_amount": "500000",
    "quotation.approval.discount_threshold_pct": "10",
    "tax.default.rate": "12",
    "tax.default.code": "VAT",
    "tax.default.mode": "exclusive",            # exclusive | inclusive
    "tax.default.type": "standard",             # standard | zero_rated | exempt
    "tax.withholding.rate": "0",
    "tax.rounding_mode": "round",
    "payment.downpayment.default_rate": "30",
    "payment.downpayment.minimum_rate": "0",
    "payment.downpayment.required": "true",
    # Governed document numbering prefixes — defaults EQUAL the historical hardcoded values,
    # so behaviour is unchanged until explicitly overridden through the config cascade.
    "numbering.booking.prefix": "BK",
    "numbering.quotation.prefix": "QN",
    "numbering.job.prefix": "JO",
    "numbering.invoice.prefix": "INV",
    # Protected-payment LIVE-FUNDS hard boundary (W9). Live custody stays OFF until BOTH external
    # prerequisites are documented as approved. Enforced centrally in marketplace_payments.
    "payments.live_protected_funds_enabled": "false",
    "payments.legal_operating_model_approved": "false",
    "payments.licensed_provider_active": "false",
    # Regulatory closure: LTFRB carrier-authority enforcement at assignment. OFF by default so existing
    # behaviour is unchanged; the owner flips this ON at go-live once carrier CPCs are recorded/verified.
    "marketplace.ltfrb_enforcement_enabled": "false",
    # Cargo Insurance / Goods Protection (orchestration; LiftHaul is not the insurer). No insurer is
    # connected by default -> quotes return MANUAL_INSURANCE_REVIEW_REQUIRED until a licensed provider
    # is configured + activated. Live coverage is never advertised until approved.
    "insurance.enabled": "true",
    "insurance.provider_active": "false",
    "insurance.max_auto_quote_amount": "500000",
    "insurance.manual_underwriting_threshold": "1000000",
    "insurance.excluded_cargo": "PROHIBITED,DANGEROUS",
    "insurance.deductible_pct": "2",
    # Secure Delivery Verification (recipient OTP is ONE factor). Enforcement OFF by default so existing
    # release flows are unchanged; policy is per-context (default / heavy / high-value), not universal.
    "delivery.verification_enforced": "false",
    "delivery.policy.default": "POD_REQUIRED",
    "delivery.policy.heavy": "POD_REQUIRED,RECIPIENT_SIGNATURE_REQUIRED,PHOTO_REQUIRED",
    "delivery.policy.high_value": "POD_REQUIRED,GEOFENCE_REQUIRED,RECIPIENT_OTP_REQUIRED,RECIPIENT_SIGNATURE_REQUIRED",
    "delivery.high_value_threshold": "1000000",
    "delivery.otp_ttl_minutes": "15",
    "delivery.otp_max_attempts": "5",
    "delivery.resend_max": "3",
    "delivery.messaging_provider_active": "false",
}


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def seed(conn, actor=None):
    """Idempotently seed permission catalog, system roles, platform config, and tenant 0."""
    for code, module, action, desc in CATALOG:
        conn.execute("INSERT INTO admin_permissions(code,module,action,description) VALUES(?,?,?,?)"
                     " ON CONFLICT(code) DO UPDATE SET module=excluded.module,"
                     " action=excluded.action, description=excluded.description",
                     (code, module, action, desc))
    def _seed_role(code, name, layer, grants):
        rid = _upsert_role(conn, PLATFORM_TENANT, code, name, layer, system_locked=1)
        for g in grants:
            conn.execute("INSERT INTO admin_role_permissions(role_id,permission_code) VALUES(?,?)"
                         " ON CONFLICT(role_id,permission_code) DO NOTHING", (rid, g))
    for code, name, layer, grants in ADMIN_ROLES:
        _seed_role(code, name, layer, grants)
    for code, (name, layer) in OPERATIONAL_ROLE_META.items():
        _seed_role(code, name, layer, set(core.PERMISSIONS.get(code, _DEFAULT_GRANT)))
    for key, val in DEFAULT_CONFIG.items():
        set_config(conn, "platform", "", key, val, actor=actor)
    # Tenant zero: RGO Machine Rigging Services
    if not get_tenant(conn, "RGO"):
        create_tenant(conn, "RGO", "RGO Machine Rigging Services", trading_name="RGO Machine Rigging",
                      actor=actor)
    conn.commit()


def _upsert_role(conn, tenant_id, code, name, layer, system_locked=0) -> int:
    row = conn.execute("SELECT id FROM admin_roles WHERE tenant_id=? AND code=?",
                       (tenant_id, code)).fetchone()
    if row:
        conn.execute("UPDATE admin_roles SET name=?, layer=?, system_locked=? WHERE id=?",
                     (name, layer, system_locked, row["id"]))
        return row["id"]
    cur = conn.execute("INSERT INTO admin_roles(tenant_id,code,name,layer,system_locked,created_at)"
                       " VALUES(?,?,?,?,?,?)", (tenant_id, code, name, layer, system_locked, _now()))
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# Tenants (C-003)
# --------------------------------------------------------------------------- #
def create_tenant(conn, code, legal_name, trading_name=None, plan="STANDARD", actor=None) -> int:
    if get_tenant(conn, code):
        raise core.ConflictError(f"tenant '{code}' already exists")
    cur = conn.execute("INSERT INTO tenants(code,legal_name,trading_name,status,plan,created_at)"
                       " VALUES(?,?,?, 'ACTIVE', ?, ?)", (code, legal_name, trading_name, plan, _now()))
    tid = cur.lastrowid
    if actor:
        core.audit(conn, actor, "TENANT_CREATED", "tenants", tid, new={"code": code})
    conn.commit()
    return tid


def get_tenant(conn, code):
    return conn.execute("SELECT * FROM tenants WHERE code=?", (code,)).fetchone()


def list_tenants(conn):
    return conn.execute("SELECT * FROM tenants ORDER BY id").fetchall()


# --------------------------------------------------------------------------- #
# Roles & permissions (C-005) — data-driven, replaces reliance on core.PERMISSIONS
# --------------------------------------------------------------------------- #
def create_role(conn, tenant_code, code, name, layer=4, grants=None, actor=None) -> int:
    """Create a tenant-scoped custom role. Enforcement follows immediately — no code change."""
    t = get_tenant(conn, tenant_code)
    if not t:
        raise core.ConflictError(f"unknown tenant '{tenant_code}'")
    existing = conn.execute("SELECT id FROM admin_roles WHERE tenant_id=? AND code=?",
                            (t["id"], code)).fetchone()
    if existing:
        raise core.ConflictError(f"role '{code}' already exists for tenant '{tenant_code}'")
    cur = conn.execute("INSERT INTO admin_roles(tenant_id,code,name,layer,system_locked,created_at)"
                       " VALUES(?,?,?,?,0,?)", (t["id"], code, name, layer, _now()))
    rid = cur.lastrowid
    for g in (grants or set()):
        grant_permission(conn, rid, g)
    if actor:
        core.audit(conn, actor, "ROLE_UPSERTED", "admin_roles", rid, new={"code": code, "grants": sorted(grants or [])})
    conn.commit()
    return rid


# Separation-of-duties: permission pairs that must not be held by the same user.
# Only genuinely-separate-role pairs in THIS product (the finance role holds payment
# submit+verify by design, so that pair is NOT listed); super-admins ('*') are exempt.
SOD_PAIRS = [
    ("quotation.create", "quotation.approve"),      # estimator vs approver
    ("quotation.edit_draft", "quotation.approve"),
    ("quotation.revise_returned", "quotation.approve"),
    ("user_admin.manage", "audit.manage"),          # user admin vs audit admin
]
HIGH_RISK = {"quotation.approve", "payment.verify", "expense.approve", "*",
             "tenant.manage", "role_admin.manage", "role_admin.assign_privileged",
             "user_admin.manage", "security.manage"}
PRIVILEGED_ROLE_CODES = {
    "super_platform_admin", "platform_admin", "super_admin", "executive",
}
USER_ADMIN_ASSIGNABLE_ROLE_CODES = {
    "booking_quotation_administrator", "approver", "finance_admin", "finance",
    "operations_manager", "estimator", "dispatcher", "fleet_manager", "safety_officer",
    "mechanic", "driver", "operator", "operational_user", "customer",
}


def _perm_covers(perms, action):
    if "*" in perms or action in perms:
        return True
    return any(p.endswith(".*") and action.startswith(p[:-1]) for p in perms)


def sod_conflicts(perms):
    """Incompatible permission pairs held simultaneously (Item 1 SoD visibility).
    Super-administrators ('*') are the platform exception and are not flagged."""
    if "*" in perms:
        return []
    out = []
    for a, b in SOD_PAIRS:
        if _perm_covers(perms, a) and _perm_covers(perms, b):
            out.append({"a": a, "b": b, "reason": f"{a} and {b} must be separated (maker≠checker)"})
    return out


def compare_roles(conn, tenant_code, code_a, code_b):
    """Side-by-side role comparison from the persisted model (Item 1 role admin)."""
    ra, rb = role_by_code(conn, tenant_code, code_a), role_by_code(conn, tenant_code, code_b)
    if not ra or not rb:
        raise core.ConflictError("unknown role in comparison")
    ga, gb = effective_role_grants(conn, ra["id"]), effective_role_grants(conn, rb["id"])
    combined = ga | gb
    return {
        "a": {"code": code_a, "layer": ra["layer"], "system_locked": ra["system_locked"],
              "assigned_users": role_assigned_user_count(conn, ra["id"]), "grants": sorted(ga)},
        "b": {"code": code_b, "layer": rb["layer"], "system_locked": rb["system_locked"],
              "assigned_users": role_assigned_user_count(conn, rb["id"]), "grants": sorted(gb)},
        "only_a": sorted(ga - gb), "only_b": sorted(gb - ga), "shared": sorted(ga & gb),
        "high_risk": sorted(g for g in combined if g in HIGH_RISK),
        "sod_conflicts": sod_conflicts(combined)}


def role_dependency(conn, tenant_code, code):
    r = role_by_code(conn, tenant_code, code)
    if not r:
        raise core.ConflictError("unknown role")
    users = role_assigned_user_count(conn, r["id"])
    return {"code": code, "layer": r["layer"], "system_locked": bool(r["system_locked"]),
            "assigned_users": users, "protected": bool(r["system_locked"]),
            "can_archive": (not r["system_locked"]) and users == 0}


def clone_role(conn, tenant_code, src_code, new_code, name, actor=None) -> int:
    """Clone a role's grants into a new tenant role (Item 6 role administration)."""
    src = role_by_code(conn, tenant_code, src_code)
    if not src:
        raise core.ConflictError(f"unknown source role '{src_code}'")
    return create_role(conn, tenant_code, new_code, name, layer=src["layer"],
                       grants=effective_role_grants(conn, src["id"]), actor=actor)


def role_assigned_user_count(conn, role_id) -> int:
    return conn.execute("SELECT COUNT(*) c FROM admin_user_roles WHERE role_id=?", (role_id,)).fetchone()["c"]


def grant_permission(conn, role_id, permission_code, actor=None):
    role = conn.execute("SELECT * FROM admin_roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        raise core.ConflictError("unknown role")
    if role["system_locked"]:
        raise core.ForbiddenError("system role is locked; clone it to customize")
    conn.execute("INSERT INTO admin_role_permissions(role_id,permission_code) VALUES(?,?)"
                 " ON CONFLICT(role_id,permission_code) DO NOTHING", (role_id, permission_code))
    if actor:
        core.audit(conn, actor, "ROLE_PERMISSION_CHANGED", "admin_roles", role_id,
                   new={"granted": permission_code})
    conn.commit()


def revoke_permission(conn, role_id, permission_code, actor=None):
    conn.execute("DELETE FROM admin_role_permissions WHERE role_id=? AND permission_code=?",
                 (role_id, permission_code))
    if actor:
        core.audit(conn, actor, "ROLE_PERMISSION_CHANGED", "admin_roles", role_id,
                   new={"revoked": permission_code})
    conn.commit()


def list_roles(conn, tenant_code=None):
    """Global system roles (tenant 0) plus this tenant's custom roles."""
    if tenant_code is None:
        return conn.execute("SELECT * FROM admin_roles ORDER BY layer, code").fetchall()
    t = get_tenant(conn, tenant_code)
    tid = t["id"] if t else -1
    return conn.execute("SELECT * FROM admin_roles WHERE tenant_id IN (?, ?) ORDER BY layer, code",
                        (PLATFORM_TENANT, tid)).fetchall()


def role_by_code(conn, tenant_code, code):
    t = get_tenant(conn, tenant_code)
    tid = t["id"] if t else PLATFORM_TENANT
    return conn.execute("SELECT * FROM admin_roles WHERE code=? AND tenant_id IN (?, ?) ORDER BY tenant_id DESC",
                        (code, tid, PLATFORM_TENANT)).fetchone()


def _covers(actor_perms, grant):
    if "*" in actor_perms or grant in actor_perms:
        return True
    return any(p.endswith(".*") and grant.startswith(p[:-1]) for p in actor_perms)


def _guard_role_assignment(conn, actor, role_id):
    """Admin guardrails (Item 6): a non-platform admin may not assign platform-layer roles,
    nor grant any permission beyond their own authority (self-elevation prevention)."""
    perms = actor.get("perms") or core.PERMISSIONS.get(actor.get("role"), set())
    role = conn.execute("SELECT * FROM admin_roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        raise core.ConflictError("unknown role")
    if role["code"] in PRIVILEGED_ROLE_CODES and not _perm_covers(perms, "role_admin.assign_privileged"):
        raise core.ForbiddenError("assigning Super Administrator, platform, or executive roles requires additional approval")
    if "*" in perms:
        return
    if role["layer"] == 1:
        raise core.ForbiddenError("only a platform administrator may assign platform-layer roles")
    if (_perm_covers(perms, "user_admin.assign_roles")
            and role["code"] in USER_ADMIN_ASSIGNABLE_ROLE_CODES):
        return
    for g in effective_role_grants(conn, role_id):
        if not _covers(perms, g):
            raise core.ForbiddenError(f"cannot grant '{g}' beyond your own authority (self-elevation blocked)")


def _is_super_admin(conn, user_id):
    return bool(conn.execute(
        "SELECT 1 FROM admin_user_roles ur JOIN admin_roles r ON r.id=ur.role_id"
        " WHERE ur.user_id=? AND r.code='super_platform_admin'", (user_id,)).fetchone())


def _other_active_super_admin(conn, user_id):
    return bool(conn.execute(
        "SELECT 1 FROM admin_user_roles ur JOIN admin_roles r ON r.id=ur.role_id"
        " JOIN users u ON u.id=ur.user_id WHERE r.code='super_platform_admin'"
        " AND u.status='ACTIVE' AND u.id<>?", (user_id,)).fetchone())


def assignment_sod_conflicts(conn, user_id, role_id):
    """SoD conflicts that WOULD arise from adding role_id to the user (before saving)."""
    current = effective_permissions(conn, user_id)
    adding = effective_role_grants(conn, role_id)
    before = {(a, b) for a, b in SOD_PAIRS if _perm_covers(current, a) and _perm_covers(current, b)}
    combined = current | adding
    after = sod_conflicts(combined)
    return [c for c in after if (c["a"], c["b"]) not in before]     # only NEW conflicts


def assign_role(conn, user_id, role_id, actor=None, allow_sod_exception=False, reason=None):
    if actor is not None:
        target = conn.execute("SELECT id,tenant_id FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise core.ConflictError("unknown user")
        perms = actor.get("perms") or core.PERMISSIONS.get(actor.get("role"), set())
        actor_tenant = actor.get("tenant_id")
        if actor_tenant is not None and target["tenant_id"] != actor_tenant and "*" not in perms:
            raise core.ForbiddenError("user administrators may manage only users in their own tenant")
        already = conn.execute(
            "SELECT 1 FROM admin_user_roles WHERE user_id=? AND role_id=?", (user_id, role_id)
        ).fetchone()
        if actor.get("id") == user_id and not already and "*" not in perms:
            raise core.ForbiddenError("self-assignment of additional roles is prohibited")
        _guard_role_assignment(conn, actor, role_id)
    conflicts = assignment_sod_conflicts(conn, user_id, role_id)
    if conflicts and not allow_sod_exception:
        raise core.ForbiddenError("separation-of-duties conflict: " +
                                  "; ".join(c["reason"] for c in conflicts))
    conn.execute("INSERT INTO admin_user_roles(user_id,role_id) VALUES(?,?)"
                 " ON CONFLICT(user_id,role_id) DO NOTHING", (user_id, role_id))
    if actor:
        core.audit(conn, actor, "USER_ROLE_GRANTED", "admin_user_roles", user_id,
                   new={"role_id": role_id, "sod_exception": bool(conflicts), "reason": reason})
    conn.commit()


def unassign_role(conn, user_id, role_id, actor=None):
    conn.execute("DELETE FROM admin_user_roles WHERE user_id=? AND role_id=?", (user_id, role_id))
    conn.commit()


def effective_role_grants(conn, role_id) -> set:
    """Grant patterns held by a single role (used for parity checks / role editor)."""
    rows = conn.execute("SELECT permission_code p FROM admin_role_permissions WHERE role_id=?",
                        (role_id,)).fetchall()
    return {r["p"] for r in rows}


def effective_permissions(conn, user_id) -> set:
    """Union of all grant patterns from every role assigned to the user."""
    rows = conn.execute(
        "SELECT rp.permission_code p FROM admin_user_roles ur"
        " JOIN admin_role_permissions rp ON rp.role_id = ur.role_id WHERE ur.user_id=?",
        (user_id,)).fetchall()
    return {r["p"] for r in rows}


def has_permission(conn, user_id, action) -> bool:
    """Data-driven equivalent of core.can — same wildcard grammar, but grants live in the DB."""
    return _match(effective_permissions(conn, user_id), action)


def apply_rbac(conn, actor):
    """Enrich an actor with DB-sourced permissions per the `iam.rbac_source` flag (C-005).

    Reversible cutover switch (a platform_config value, admin-owned):
      * legacy  — leave the actor untouched; core.can uses in-code PERMISSIONS.
      * hybrid  — use DB permissions IF the user has assigned roles, else legacy.
      * db      — always use DB permissions (empty set = deny-all).
    Returns the (possibly enriched) actor. server._actor calls this on every request.
    """
    src, _ = resolve_config(conn, "iam.rbac_source")
    src = (src or "legacy").lower()
    if src in ("db", "hybrid") and actor and "id" in actor:
        perms = effective_permissions(conn, actor["id"])
        if perms or src == "db":
            actor["perms"] = perms
    return actor


def backfill_user_roles(conn, tenant_code="RGO", actor=None) -> int:
    """Assign each existing user the system role matching their legacy users.role.

    Completes the cutover at parity (system-role grants == assembled core.PERMISSIONS).
    Idempotent; returns the number of new assignments made."""
    made = 0
    for u in conn.execute("SELECT id, role FROM users").fetchall():
        role = role_by_code(conn, tenant_code, u["role"])
        if not role:
            continue
        exists = conn.execute("SELECT 1 FROM admin_user_roles WHERE user_id=? AND role_id=?",
                              (u["id"], role["id"])).fetchone()
        if not exists:
            assign_role(conn, u["id"], role["id"], actor=actor)
            made += 1
    return made


def _match(patterns, action) -> bool:
    for p in patterns:
        if p == "*" or p == action:
            return True
        if p.endswith(".*") and action.startswith(p[:-1]):
            return True
    return False


# --------------------------------------------------------------------------- #
# Configuration cascade (C-008): platform -> tenant -> unit -> user (most specific wins)
# --------------------------------------------------------------------------- #
# Config scopes: platform/tenant/user (base) + the org levels added by C-004.
_CONFIG_SCOPES = ("platform", "tenant", "business_unit", "branch", "department", "team", "unit", "user")


def set_config(conn, scope, scope_ref, key, value, actor=None, effective_to=None):
    if scope not in _CONFIG_SCOPES:
        raise core.ConflictError(f"invalid config scope '{scope}'")
    import config_registry
    config_registry.validate(conn, key, value, scope)     # typed/range/scope validation (Phase 2)
    conn.execute("INSERT INTO platform_config(scope,scope_ref,key,value,effective_to,updated_by,updated_at)"
                 " VALUES(?,?,?,?,?,?,?)"
                 " ON CONFLICT(scope,scope_ref,key) DO UPDATE SET value=excluded.value,"
                 " effective_to=excluded.effective_to, updated_by=excluded.updated_by,"
                 " updated_at=excluded.updated_at",
                 (scope, scope_ref or "", key, str(value), effective_to, (actor or {}).get("id"), _now()))
    if actor:
        core.audit(conn, actor, "CONFIG_SET", "platform_config", 0,
                   new={"scope": scope, "ref": scope_ref, "key": key, "value": str(value)})
    conn.commit()


def resolve_config(conn, key, tenant=None, unit=None, user=None):
    """Return (value, source_scope) using most-specific-wins; (None, None) if unset."""
    r = resolve_config_chain(conn, key, [("user", user), ("unit", unit), ("tenant", tenant)])
    return r["value"], r["scope"]


def resolve_config_chain(conn, key, chain):
    """Resolve `key` down an ordered scope chain (most-specific first); platform is always
    the final fallback. Skips expired rows (effective_to < today). Returns a dict with the
    effective value, source scope + ref, updated_at, and the fallback path walked (C-004 §11)."""
    today = datetime.date.today().isoformat()
    path = []
    for scope, ref in list(chain) + [("platform", "")]:
        if ref is None:
            continue
        path.append(f"{scope}:{ref or '-'}")
        row = conn.execute(
            "SELECT value, updated_at, effective_to FROM platform_config"
            " WHERE scope=? AND scope_ref=? AND key=?", (scope, ref, key)).fetchone()
        if row and (row["effective_to"] is None or row["effective_to"] >= today):
            return {"value": row["value"], "scope": scope, "scope_ref": ref,
                    "updated_at": row["updated_at"], "fallback_path": path}
    return {"value": None, "scope": None, "scope_ref": None, "updated_at": None, "fallback_path": path}


# --------------------------------------------------------------------------- #
# User lifecycle administration (C-006)
# Full lifecycle above core.create_user: invite -> active <-> suspended/locked ->
# deactivated (offboard, soft). Non-active users cannot authenticate or act
# (enforced in core.login / core.actor_for); state changes revoke live sessions.
# --------------------------------------------------------------------------- #
STATUSES = {"ACTIVE", "SUSPENDED", "LOCKED", "DEACTIVATED"}


def create_user(conn, actor, email, password, role, name=None, tenant_code="RGO", customer_id=None) -> int:
    """Invite/create a user AND assign the matching system role so DB-RBAC governs them."""
    validate_password(conn, password, tenant=tenant_code)          # C-007 policy
    tenant = get_tenant(conn, tenant_code)
    if not tenant:
        raise core.ConflictError("unknown tenant")
    perms = (actor or {}).get("perms") or core.PERMISSIONS.get((actor or {}).get("role"), set())
    if actor and actor.get("tenant_id") is not None and actor.get("tenant_id") != tenant["id"] and "*" not in perms:
        raise core.ForbiddenError("user administrators may create users only in their own tenant")
    uid = core.create_user(conn, email, password, role, name, customer_id)
    conn.execute("UPDATE users SET tenant_id=? WHERE id=?", (tenant["id"], uid))
    r = role_by_code(conn, tenant_code, role)
    if r:
        assign_role(conn, uid, r["id"], actor=actor)
    if actor:
        core.audit(conn, actor, "USER_INVITED", "users", uid, new={"email": email, "role": role})
    conn.commit()
    return uid


def _guard_user_scope(conn, actor, user_id):
    if actor is None or actor.get("tenant_id") is None:
        return
    if "*" in actor.get("perms", set()):
        return
    row = conn.execute("SELECT tenant_id FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise core.ConflictError("unknown user")
    if row["tenant_id"] != actor.get("tenant_id"):
        raise core.ForbiddenError("user administrators may manage only users in their own tenant")


def set_status(conn, actor, user_id, status):
    _guard_user_scope(conn, actor, user_id)
    status = status.upper()
    if status not in STATUSES:
        raise core.ConflictError(f"invalid user status '{status}'")
    old = conn.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
    if not old:
        raise core.ConflictError("unknown user")
    if status != "ACTIVE" and _is_super_admin(conn, user_id) and not _other_active_super_admin(conn, user_id):
        raise core.ForbiddenError("cannot deactivate the last active Super Platform Administrator")
    conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
    if status != "ACTIVE":
        revoke_sessions(conn, user_id)                     # kill live sessions immediately
    if actor:
        core.audit(conn, actor, "USER_STATUS_CHANGED", "users", user_id,
                   old={"status": old["status"]}, new={"status": status})
    conn.commit()


def suspend_user(conn, actor, uid):    set_status(conn, actor, uid, "SUSPENDED")
def activate_user(conn, actor, uid):   set_status(conn, actor, uid, "ACTIVE")
def lock_user(conn, actor, uid):       set_status(conn, actor, uid, "LOCKED")
def unlock_user(conn, actor, uid):     set_status(conn, actor, uid, "ACTIVE")
def deactivate_user(conn, actor, uid): set_status(conn, actor, uid, "DEACTIVATED")   # offboard (soft)


def reset_password(conn, actor, user_id, new_password):
    """Admin-initiated password reset: set a new hash and revoke all sessions."""
    _guard_user_scope(conn, actor, user_id)
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        raise core.ConflictError("unknown user")
    validate_password(conn, new_password)                          # C-007 policy
    conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (core.hash_pw(new_password), user_id))
    revoke_sessions(conn, user_id)
    if actor:
        core.audit(conn, actor, "USER_PASSWORD_RESET", "users", user_id)
    conn.commit()


def revoke_sessions(conn, user_id) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit()
    return getattr(cur, "rowcount", 0) or 0


def list_users(conn, actor=None):
    perms = (actor or {}).get("perms") or core.PERMISSIONS.get((actor or {}).get("role"), set())
    if actor is not None and actor.get("tenant_id") is not None and "*" not in perms:
        return conn.execute(
            "SELECT id,email,role,name,status,last_login_at,created_at FROM users WHERE tenant_id=? ORDER BY id",
            (actor.get("tenant_id"),),
        ).fetchall()
    return conn.execute("SELECT id,email,role,name,status,last_login_at,created_at"
                        " FROM users ORDER BY id").fetchall()


def get_user(conn, user_id):
    return conn.execute("SELECT id,email,role,name,status,last_login_at,created_at"
                        " FROM users WHERE id=?", (user_id,)).fetchone()


def user_roles(conn, user_id):
    return conn.execute(
        "SELECT r.code,r.name,r.layer FROM admin_user_roles ur"
        " JOIN admin_roles r ON r.id=ur.role_id WHERE ur.user_id=? ORDER BY r.layer, r.code",
        (user_id,)).fetchall()


def permission_review(conn, user_id) -> set:
    """The effective permission set an admin sees when reviewing a user (Vol2 §3.4)."""
    return effective_permissions(conn, user_id)


def user_audit(conn, user_id, limit=100):
    return conn.execute(
        "SELECT ts,actor,role,action,new_value FROM audit_logs"
        " WHERE entity='users' AND entity_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)).fetchall()


# --------------------------------------------------------------------------- #
# Authentication policy, MFA & session governance (C-007)
# All policy is admin-owned config (ED-004), resolved through the cascade.
# --------------------------------------------------------------------------- #
def password_policy(conn, tenant=None) -> dict:
    def g(key, default):
        v, _ = resolve_config(conn, key, tenant=tenant)
        return v if v is not None else default
    return {"min_length": int(g("auth.pw_min_length", "10")),
            "complexity": g("auth.pw_require_complexity", "true").lower() == "true"}


def validate_password(conn, pw, tenant=None):
    p = password_policy(conn, tenant)
    if not pw or len(pw) < p["min_length"]:
        raise core.ValidationError(f"password must be at least {p['min_length']} characters")
    if p["complexity"] and (not re.search(r"[A-Z]", pw) or not re.search(r"[a-z]", pw)
                            or not re.search(r"\d", pw)):
        raise core.ValidationError("password must include an upper-case letter, a lower-case letter and a digit")


# ---- login history + lockout (persistent, tenant-aware) -------------------- #
def record_login(conn, email, success, reason, ip=None):
    try:
        u = conn.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
        conn.execute("INSERT INTO login_history(user_id,email,success,reason,ip,ts) VALUES(?,?,?,?,?,?)",
                     (u["id"] if u else None, email.lower(), 1 if success else 0, reason, ip, _now()))
        conn.commit()
    except Exception:
        pass                                               # degrade gracefully on a non-seeded conn


def login_locked(conn, email, tenant=None) -> bool:
    """Locked when consecutive recent failures (since the last success, within the
    window) reach the threshold. Threshold 0 disables lockout."""
    try:
        thr, _ = resolve_config(conn, "auth.lockout_threshold", tenant=tenant)
        win, _ = resolve_config(conn, "auth.lockout_window_min", tenant=tenant)
        thr, win = int(thr or "5"), int(win or "15")
        if thr <= 0:
            return False
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(minutes=win)).isoformat(timespec="seconds")
        rows = conn.execute("SELECT success FROM login_history WHERE email=? AND ts>=? ORDER BY id DESC",
                            (email.lower(), cutoff)).fetchall()
        fails = 0
        for r in rows:
            if r["success"]:
                break
            fails += 1
        return fails >= thr
    except Exception:
        return False


def list_login_history(conn, user_id=None, email=None, limit=100):
    if user_id is not None:
        return conn.execute("SELECT * FROM login_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
                            (user_id, limit)).fetchall()
    if email:
        return conn.execute("SELECT * FROM login_history WHERE email=? ORDER BY id DESC LIMIT ?",
                            (email.lower(), limit)).fetchall()
    return conn.execute("SELECT * FROM login_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ---- MFA (TOTP, RFC 6238, stdlib only) ------------------------------------- #
def _b32secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32, counter) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def _totp(secret_b32, t=None, step=30) -> str:
    t = time.time() if t is None else t
    return _hotp(secret_b32, int(t // step))


def verify_totp(secret_b32, code, t=None, step=30, window=1) -> bool:
    t = time.time() if t is None else t
    code = str(code).strip()
    base = int(t // step)
    return any(_hotp(secret_b32, base + w) == code
               for w in range(-window, window + 1) if base + w >= 0)


def enroll_mfa(conn, user_id) -> str:
    """Begin MFA enrollment: create/replace an unconfirmed secret; return it for the
    authenticator app (also expose as an otpauth:// URI via mfa_provisioning_uri)."""
    secret = _b32secret()
    conn.execute("INSERT INTO mfa_enrollments(user_id,secret,confirmed,enrolled_at) VALUES(?,?,0,?)"
                 " ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret, confirmed=0,"
                 " enrolled_at=excluded.enrolled_at", (user_id, secret, _now()))
    conn.commit()
    return secret


def mfa_provisioning_uri(secret, email, issuer="LiftHaul OS") -> str:
    return f"otpauth://totp/{issuer}:{email}?secret={secret}&issuer={issuer}"


def confirm_mfa(conn, user_id, code) -> bool:
    row = conn.execute("SELECT secret FROM mfa_enrollments WHERE user_id=?", (user_id,)).fetchone()
    if not row or not verify_totp(row["secret"], code):
        raise core.AuthError("invalid MFA code")
    conn.execute("UPDATE mfa_enrollments SET confirmed=1 WHERE user_id=?", (user_id,))
    conn.commit()
    return True


def verify_mfa(conn, user_id, code) -> bool:
    row = conn.execute("SELECT secret,confirmed FROM mfa_enrollments WHERE user_id=?", (user_id,)).fetchone()
    return bool(row and row["confirmed"] and verify_totp(row["secret"], code))


def mfa_enrolled(conn, user_id) -> bool:
    try:
        row = conn.execute("SELECT confirmed FROM mfa_enrollments WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["confirmed"])
    except Exception:
        return False


def disable_mfa(conn, actor, user_id):
    conn.execute("DELETE FROM mfa_enrollments WHERE user_id=?", (user_id,))
    if actor:
        core.audit(conn, actor, "MFA_DISABLED", "users", user_id)
    conn.commit()


def mfa_policy(conn, tenant=None) -> str:
    v, _ = resolve_config(conn, "auth.mfa_policy", tenant=tenant)
    return (v or "optional").lower()


def mfa_required_for(conn, user_row, tenant=None) -> bool:
    pol = mfa_policy(conn, tenant)
    if pol == "required":
        return True
    if pol == "off":
        return False
    return mfa_enrolled(conn, user_row["id"])              # optional: enforced once enrolled


# ---- guarded login (lockout -> credentials -> status -> MFA -> session) ----- #
def guarded_login(conn, email, password, ip=None, tenant="RGO", mfa_code=None) -> str:
    if login_locked(conn, email, tenant):
        record_login(conn, email, False, "locked", ip)
        raise core.AuthError("account temporarily locked — too many failed attempts")
    row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
    if not row or not core.verify_pw(password, row["pw_hash"]):
        record_login(conn, email, False, "invalid_credentials", ip)
        raise core.AuthError("invalid credentials")
    if core._user_status(row) != "ACTIVE":
        record_login(conn, email, False, "inactive", ip)
        raise core.AuthError("account is not active")
    if mfa_required_for(conn, row, tenant):
        if not mfa_code:
            record_login(conn, email, False, "mfa_required", ip)
            raise core.AuthError("MFA code required")
        if not verify_mfa(conn, row["id"], mfa_code):
            record_login(conn, email, False, "mfa_invalid", ip)
            raise core.AuthError("invalid MFA code")
    token = core.login(conn, email, password)              # verifies again + creates session
    if ip is not None:
        conn.execute("UPDATE sessions SET ip=?, last_seen=? WHERE token=?", (ip, _now(), token))
        conn.commit()
    record_login(conn, email, True, "ok", ip)
    return token


# ---- session administration ------------------------------------------------ #
def list_sessions(conn, user_id=None):
    if user_id is not None:
        return conn.execute("SELECT token,user_id,ip,created_at,last_seen FROM sessions"
                            " WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
    return conn.execute("SELECT token,user_id,ip,created_at,last_seen FROM sessions"
                        " ORDER BY created_at DESC").fetchall()


def revoke_session(conn, token, actor=None):
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    if actor:
        core.audit(conn, actor, "SESSION_REVOKED", "sessions", 0, new={"token": token[:6] + "…"})
    conn.commit()
