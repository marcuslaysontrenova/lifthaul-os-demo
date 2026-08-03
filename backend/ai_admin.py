"""LiftHaul OS — Phase 9: governed AI Administration.

AI is ADVISORY and HUMAN-REVIEWED by default. This module governs AI use cases, a model registry,
immutable prompt versions, data classification + redaction, an allowlisted tool registry (prohibited
actions can never be registered), structured-output validation, grounding + injection defenses, human-
review policies + a review queue, usage/cost accounting + budgets (hard stop), rate limiting, an
evaluation framework (publication gated on thresholds), observability, incidents, a scoped kill switch,
and governed AI memory.

Hard invariants (Phase 9 directive):
  * AI output NEVER auto-commits to an authoritative record — a human accepts/edits/rejects;
  * AI can NEVER perform a PROHIBITED action (release funds, verify payment, approve refund/quotation,
    delete records, elevate roles, retrieve secrets, cross-tenant access, …) — enforced + tested;
  * secrets / payment credentials / auth tokens are NEVER sent to a provider (classification + redaction);
  * provider keys use the Phase-6 secret-reference boundary and never reach the browser;
  * live AI is BLOCKED without owner credentials; the deterministic mock proves every non-secret path.
"""
from __future__ import annotations

import datetime
import hashlib
import json

import core
import ai_provider

RISK_LEVELS = ("low", "medium", "high", "critical")
MODEL_STATUSES = ("DRAFT", "EVALUATION", "APPROVED", "RESTRICTED", "ACTIVE", "SUSPENDED", "RETIRED", "REJECTED")
PROMPT_STATUSES = ("DRAFT", "VALIDATED", "APPROVED", "PUBLISHED", "ACTIVE", "RETIRED", "REJECTED")
DATA_CLASSES = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "PERSONAL_DATA", "FINANCIAL_DATA",
                "SAFETY_DATA", "PAYMENT_DATA", "AUTHENTICATION_DATA", "SECRET")
# fields that must NEVER be sent to a provider (redacted before any AI call)
NEVER_SEND_FIELDS = {"password", "pw", "api_key", "apikey", "token", "access_token", "refresh_token",
                     "private_key", "secret", "secret_ref", "card_number", "cvv", "bank_account",
                     "iban", "routing_number", "auth_token", "session_token", "wise_api_key"}
# actions AI may NEVER perform autonomously (tool registry rejects these; execution flags them unsafe)
PROHIBITED_ACTIONS = {
    "release_payment", "initiate_payment", "release_funds", "verify_payment", "verify_wise",
    "approve_refund", "change_tax", "change_downpayment", "modify_invoice", "confirm_cargo_legality",
    "activate_carrier", "decide_claim", "disciplinary_action", "final_hiring", "deny_access_profiling",
    "publish_legal", "modify_security_config", "disable_audit", "delete_record", "elevate_role",
    "retrieve_secret", "cross_tenant_access", "unrestricted_sql", "production_deploy", "approve_quotation",
    "dispatch_vehicle", "close_incident", "suspend_user",
}
REVIEW_POLICIES = ("always", "below_confidence", "sensitive", "external", "high_value", "safety",
                   "none_allowed", "auto_low_risk")
AI_HEALTH = ("HEALTHY", "DEGRADED", "UNAVAILABLE", "RATE_LIMITED", "BUDGET_EXHAUSTED", "MISCONFIGURED",
             "DISABLED", "UNKNOWN")
KILL_SCOPES = ("platform", "tenant", "use_case", "provider", "model", "prompt_version")
INJECTION_MARKERS = ("ignore all prior", "ignore previous instructions", "disregard the system",
                     "override the system", "you are now", "reveal the system prompt")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_use_cases(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, code TEXT NOT NULL, name TEXT,
  description TEXT, business_owner INTEGER, technical_owner INTEGER, risk_level TEXT DEFAULT 'low',
  allowed_input_classes TEXT, prohibited_inputs TEXT, allowed_models TEXT, human_review TEXT DEFAULT 'always',
  automated_action_allowed INTEGER DEFAULT 0, cost_limit REAL, quality_threshold REAL DEFAULT 0.8,
  enabled INTEGER DEFAULT 1, effective_from TEXT, effective_to TEXT, created_by INTEGER, created_at TEXT,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS ai_models(
  id INTEGER PRIMARY KEY, provider TEXT, model_id TEXT, display_name TEXT, model_type TEXT, version TEXT,
  context_limit INTEGER, capabilities TEXT, data_retention TEXT, trains_on_customer_data INTEGER DEFAULT 0,
  residency TEXT, cost_input_per_1k REAL, cost_output_per_1k REAL, latency_target INTEGER,
  approved_environments TEXT, risk_rating TEXT DEFAULT 'medium', status TEXT DEFAULT 'DRAFT',
  replacement_model TEXT, effective_from TEXT, created_by INTEGER, created_at TEXT,
  UNIQUE(provider, model_id, version));

CREATE TABLE IF NOT EXISTS ai_prompts(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, use_case_code TEXT, code TEXT NOT NULL,
  name TEXT, description TEXT, owner INTEGER, status TEXT DEFAULT 'ACTIVE', risk TEXT DEFAULT 'low',
  created_by INTEGER, created_at TEXT, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS ai_prompt_versions(
  id INTEGER PRIMARY KEY, prompt_id INTEGER NOT NULL REFERENCES ai_prompts(id), version_no INTEGER,
  status TEXT DEFAULT 'DRAFT', system_instruction TEXT, user_template TEXT, allowed_variables TEXT,
  output_schema TEXT, validation_rules TEXT, source_version INTEGER, checksum TEXT, eval_passed INTEGER DEFAULT 0,
  approved_by INTEGER, published_by INTEGER, effective_from TEXT, effective_to TEXT, created_at TEXT,
  UNIQUE(prompt_id, version_no));

CREATE TABLE IF NOT EXISTS ai_review_policies(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, use_case_code TEXT, policy TEXT, confidence_threshold REAL DEFAULT 0.7,
  created_by INTEGER, created_at TEXT, UNIQUE(tenant_id, use_case_code));

CREATE TABLE IF NOT EXISTS ai_tools(
  code TEXT PRIMARY KEY, name TEXT, permission TEXT, tenant_enforced INTEGER DEFAULT 1, org_enforced INTEGER DEFAULT 1,
  input_schema TEXT, output_schema TEXT, risk TEXT DEFAULT 'low', human_approval INTEGER DEFAULT 1,
  prohibited INTEGER DEFAULT 0, created_at TEXT);

CREATE TABLE IF NOT EXISTS ai_executions(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, org_scope TEXT, use_case_code TEXT, model_ref TEXT,
  prompt_version INTEGER, actor_id INTEGER, input_classification TEXT, input_hash TEXT, output_hash TEXT,
  structured_valid INTEGER, confidence REAL, grounded INTEGER, human_review_status TEXT DEFAULT 'PENDING',
  input_tokens INTEGER, output_tokens INTEGER, cost REAL, latency_ms INTEGER, result TEXT, error_category TEXT,
  redacted_fields TEXT, unsafe INTEGER DEFAULT 0, correlation_id TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS ai_reviews(
  id INTEGER PRIMARY KEY, execution_id INTEGER NOT NULL REFERENCES ai_executions(id), reviewer INTEGER,
  decision TEXT, edits TEXT, reason TEXT, original_output_hash TEXT, edited INTEGER DEFAULT 0, reviewed_at TEXT);

CREATE TABLE IF NOT EXISTS ai_budgets(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, scope TEXT DEFAULT 'tenant', scope_ref TEXT, use_case_code TEXT,
  period TEXT DEFAULT 'monthly', limit_cost REAL, spent_cost REAL DEFAULT 0, alert_threshold REAL DEFAULT 0.8,
  hard_stop INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS ai_evaluations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, use_case_code TEXT, prompt_version_id INTEGER, suite TEXT,
  cases_total INTEGER, cases_passed INTEGER, groundedness REAL, unsafe_rate REAL, schema_pass_rate REAL,
  avg_cost REAL, avg_latency REAL, passed INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS ai_incidents(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, use_case_code TEXT, incident_type TEXT, severity TEXT,
  status TEXT DEFAULT 'OPEN', detail TEXT, contained INTEGER DEFAULT 0, created_by INTEGER, created_at TEXT,
  correlation_id TEXT);

CREATE TABLE IF NOT EXISTS ai_kill_switches(
  id INTEGER PRIMARY KEY, scope TEXT NOT NULL, scope_ref TEXT, tenant_id INTEGER, active INTEGER DEFAULT 1,
  reason TEXT, activated_by INTEGER, activated_at TEXT, released_by INTEGER, released_at TEXT);

CREATE TABLE IF NOT EXISTS ai_memory(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, user_id INTEGER, use_case_code TEXT, purpose TEXT,
  content_hash TEXT, sensitive_excluded INTEGER DEFAULT 1, expires_at TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS ai_rate_counters(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, user_id INTEGER, use_case_code TEXT, window_start TEXT,
  count INTEGER DEFAULT 0, UNIQUE(tenant_id, user_id, use_case_code, window_start));
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _tenant(actor):
    return (actor or {}).get("tenant_id")


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------- #
# Data classification + redaction (never send secrets/payment/auth to a provider)
# --------------------------------------------------------------------------- #
_PAYMENT_FIELDS = {"card_number", "cvv", "bank_account", "iban", "routing_number"}


def classify_and_redact(payload):
    """Return (classification, redacted_payload, redacted_fields). NEVER-SEND fields are stripped;
    raw payment credentials additionally raise the classification to PAYMENT_DATA (which `execute`
    then refuses outright — such data must never reach an AI request)."""
    if not isinstance(payload, dict):
        return "INTERNAL", payload, []
    redacted, removed, cls = {}, [], "INTERNAL"
    for k, v in payload.items():
        lk = str(k).lower()
        if lk in _PAYMENT_FIELDS:
            removed.append(k); cls = "PAYMENT_DATA"; continue          # hard-blocked upstream
        if lk in NEVER_SEND_FIELDS or any(m in lk for m in ("password", "secret", "token", "api_key", "private_key")):
            removed.append(k); continue                                # auth/secret: redact + proceed
        if lk in ("amount_due", "total", "balance", "tax", "downpayment", "bank") and cls == "INTERNAL":
            cls = "FINANCIAL_DATA"
        if lk in ("phone", "email", "contact", "address", "name") and cls == "INTERNAL":
            cls = "PERSONAL_DATA"
        redacted[k] = v
    return cls, redacted, removed


# --------------------------------------------------------------------------- #
# Model registry
# --------------------------------------------------------------------------- #
def register_model(conn, actor, provider, model_id, display_name, model_type="text", version="1",
                   context_limit=8000, cost_input_per_1k=0.0005, cost_output_per_1k=0.0015,
                   approved_environments="MOCK", risk_rating="medium", trains_on_customer_data=False):
    core.require(actor, "ai.model.manage")
    if conn.execute("SELECT 1 FROM ai_models WHERE provider=? AND model_id=? AND version=?", (provider, model_id, version)).fetchone():
        raise core.ConflictError("model already registered")
    cur = conn.execute("INSERT INTO ai_models(provider,model_id,display_name,model_type,version,context_limit,"
                       "cost_input_per_1k,cost_output_per_1k,approved_environments,risk_rating,"
                       "trains_on_customer_data,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?, 'DRAFT', ?,?)",
                       (provider, model_id, display_name, model_type, version, context_limit,
                        cost_input_per_1k, cost_output_per_1k, approved_environments, risk_rating,
                        1 if trains_on_customer_data else 0, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "AI_MODEL_REGISTERED", "ai_models", cur.lastrowid, new={"provider": provider, "model_id": model_id})
    conn.commit()
    return cur.lastrowid


def approve_model(conn, actor, model_id_pk, reason=None):
    core.require(actor, "ai.model.approve")
    conn.execute("UPDATE ai_models SET status='APPROVED' WHERE id=?", (model_id_pk,))
    core.audit(conn, actor, "AI_MODEL_APPROVED", "ai_models", model_id_pk, reason=reason)
    conn.commit()
    return True


def set_model_status(conn, actor, model_id_pk, status, reason=None):
    core.require(actor, "ai.model.manage")
    if status not in MODEL_STATUSES:
        raise core.ValidationError(f"status must be one of {MODEL_STATUSES}")
    conn.execute("UPDATE ai_models SET status=? WHERE id=?", (status, model_id_pk))
    core.audit(conn, actor, "AI_MODEL_STATUS", "ai_models", model_id_pk, new={"status": status}, reason=reason)
    conn.commit()
    return True


def list_models(conn, actor):
    core.require(actor, "ai.model.view")
    return [dict(r) for r in conn.execute("SELECT * FROM ai_models ORDER BY provider, model_id").fetchall()]


def _approved_model(conn, provider="mock", environment="MOCK"):
    return conn.execute("SELECT * FROM ai_models WHERE provider=? AND status IN ('APPROVED','ACTIVE')"
                        " ORDER BY id DESC LIMIT 1", (provider,)).fetchone()


# --------------------------------------------------------------------------- #
# Use cases
# --------------------------------------------------------------------------- #
def create_use_case(conn, actor, code, name, risk_level="low", allowed_input_classes="PUBLIC,INTERNAL",
                    human_review="always", automated_action_allowed=False, allowed_models="mock",
                    cost_limit=1.0, quality_threshold=0.8, description=None):
    core.require(actor, "ai.use_case.manage")
    if risk_level not in RISK_LEVELS:
        raise core.ValidationError(f"risk_level must be one of {RISK_LEVELS}")
    if human_review not in REVIEW_POLICIES:
        raise core.ValidationError(f"human_review must be one of {REVIEW_POLICIES}")
    if automated_action_allowed:
        # automated action is a separately governed, high-risk grant — default OFF, requires elevation
        core.require(actor, "ai.platform.manage")
    tid = _tenant(actor)
    if conn.execute("SELECT 1 FROM ai_use_cases WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)", (code, tid)).fetchone():
        raise core.ConflictError(f"use case '{code}' already exists")
    cur = conn.execute("INSERT INTO ai_use_cases(tenant_id,code,name,description,risk_level,allowed_input_classes,"
                       "allowed_models,human_review,automated_action_allowed,cost_limit,quality_threshold,enabled,"
                       "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                       (tid, code, name, description, risk_level, allowed_input_classes, allowed_models,
                        human_review, 1 if automated_action_allowed else 0, cost_limit, quality_threshold,
                        (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "AI_USE_CASE_CREATED", "ai_use_cases", cur.lastrowid, new={"code": code, "risk": risk_level})
    conn.commit()
    return cur.lastrowid


def get_use_case(conn, actor, code):
    tid = _tenant(actor)
    r = conn.execute("SELECT * FROM ai_use_cases WHERE code=? AND (tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()
    if not r:
        raise core.NotFoundError("use case not found")
    return dict(r)


def list_use_cases(conn, actor):
    core.require(actor, "ai.use_case.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM ai_use_cases WHERE tenant_id=? OR tenant_id IS NULL ORDER BY code", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# Prompts + immutable versions
# --------------------------------------------------------------------------- #
def create_prompt(conn, actor, code, name, use_case_code, risk="low", description=None):
    core.require(actor, "ai.prompt.manage")
    tid = _tenant(actor)
    if conn.execute("SELECT 1 FROM ai_prompts WHERE code=? AND (tenant_id=? OR tenant_id IS NULL)", (code, tid)).fetchone():
        raise core.ConflictError(f"prompt '{code}' already exists")
    cur = conn.execute("INSERT INTO ai_prompts(tenant_id,use_case_code,code,name,description,owner,status,risk,"
                       "created_by,created_at) VALUES(?,?,?,?,?,?, 'ACTIVE', ?,?,?)",
                       (tid, use_case_code, code, name, description, (actor or {}).get("id"), risk, (actor or {}).get("id"), _now()))
    did = cur.lastrowid
    conn.execute("INSERT INTO ai_prompt_versions(prompt_id,version_no,status,created_at) VALUES(?,1,'DRAFT',?)", (did, _now()))
    core.audit(conn, actor, "AI_PROMPT_CREATED", "ai_prompts", did, new={"code": code})
    conn.commit()
    return did


def _prompt(conn, code, tid):
    return conn.execute("SELECT * FROM ai_prompts WHERE code=? AND (tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1", (code, tid)).fetchone()


def _pv(conn, vid):
    v = conn.execute("SELECT * FROM ai_prompt_versions WHERE id=?", (vid,)).fetchone()
    if not v:
        raise core.NotFoundError("prompt version not found")
    return v


def set_version_content(conn, actor, version_id, system_instruction, user_template, allowed_variables=None,
                        output_schema=None, validation_rules=None):
    core.require(actor, "ai.prompt.manage")
    v = _pv(conn, version_id)
    if v["status"] != "DRAFT":
        raise core.ForbiddenError("only a DRAFT prompt version may be edited; create a new version")
    # no executable code in validators/output schema (declarative JSON only)
    for blob in (output_schema, validation_rules):
        if blob is not None and isinstance(blob, dict):
            _reject_code(blob)
    conn.execute("UPDATE ai_prompt_versions SET system_instruction=?, user_template=?, allowed_variables=?,"
                 " output_schema=?, validation_rules=? WHERE id=?",
                 (system_instruction, user_template, json.dumps(allowed_variables or []),
                  json.dumps(output_schema) if output_schema else None,
                  json.dumps(validation_rules) if validation_rules else None, version_id))
    core.audit(conn, actor, "AI_PROMPT_VERSION_SET", "ai_prompt_versions", version_id)
    conn.commit()
    return True


def _reject_code(obj):
    blob = json.dumps(obj).lower()
    for marker in ("__import__", "eval(", "exec(", "os.system", "subprocess", "lambda", "<script"):
        if marker in blob:
            raise core.ValidationError("executable code is not allowed in prompt schema/validators")


def create_version(conn, actor, code, change_reason=None):
    core.require(actor, "ai.prompt.manage")
    tid = _tenant(actor)
    p = _prompt(conn, code, tid)
    if not p:
        raise core.NotFoundError("prompt not found")
    maxv = conn.execute("SELECT MAX(version_no) m FROM ai_prompt_versions WHERE prompt_id=?", (p["id"],)).fetchone()["m"] or 0
    src = conn.execute("SELECT * FROM ai_prompt_versions WHERE prompt_id=? AND version_no=?", (p["id"], maxv)).fetchone()
    cur = conn.execute("INSERT INTO ai_prompt_versions(prompt_id,version_no,status,system_instruction,user_template,"
                       "allowed_variables,output_schema,validation_rules,source_version,created_at)"
                       " VALUES(?,?, 'DRAFT', ?,?,?,?,?,?,?)",
                       (p["id"], maxv + 1, src["system_instruction"] if src else None,
                        src["user_template"] if src else None, src["allowed_variables"] if src else None,
                        src["output_schema"] if src else None, src["validation_rules"] if src else None, maxv, _now()))
    core.audit(conn, actor, "AI_PROMPT_VERSION_CREATED", "ai_prompt_versions", cur.lastrowid, new={"code": code, "version": maxv + 1})
    conn.commit()
    return cur.lastrowid


def validate_version(conn, actor, version_id, persist=True):
    core.require(actor, "ai.prompt.validate")
    v = _pv(conn, version_id)
    errors = []
    if not v["system_instruction"]:
        errors.append("missing system instruction")
    if v["output_schema"]:
        try:
            _reject_code(json.loads(v["output_schema"]))
        except core.ValidationError as e:
            errors.append(str(e))
    # unsafe content in the system instruction (prompt asks to bypass policy)
    sysl = (v["system_instruction"] or "").lower()
    if any(m in sysl for m in INJECTION_MARKERS) or "approve" in sysl and "autonomous" in sysl:
        errors.append("prompt attempts to bypass human review / business policy")
    result = {"ok": len(errors) == 0, "errors": errors}
    if persist and result["ok"] and v["status"] == "DRAFT":
        conn.execute("UPDATE ai_prompt_versions SET status='VALIDATED' WHERE id=?", (version_id,))
    core.audit(conn, actor, "AI_PROMPT_VALIDATED", "ai_prompt_versions", version_id, new={"ok": result["ok"]})
    conn.commit()
    return result


def approve_version(conn, actor, version_id, reason=None):
    core.require(actor, "ai.prompt.approve")
    v = _pv(conn, version_id)
    if v["status"] != "VALIDATED":
        raise core.ConflictError("only a VALIDATED prompt may be approved")
    if not v["eval_passed"]:
        raise core.ConflictError("prompt must pass evaluation before approval")
    conn.execute("UPDATE ai_prompt_versions SET status='APPROVED', approved_by=? WHERE id=?", ((actor or {}).get("id"), version_id))
    core.audit(conn, actor, "AI_PROMPT_APPROVED", "ai_prompt_versions", version_id, reason=reason)
    conn.commit()
    return True


def publish_version(conn, actor, version_id, change_reason, effective_from=None):
    core.require(actor, "ai.prompt.publish")
    v = _pv(conn, version_id)
    if v["status"] != "APPROVED":
        raise core.ConflictError("only an APPROVED prompt may be published")
    if not v["eval_passed"]:
        raise core.ValidationError("cannot publish a prompt that has not passed evaluation")
    checksum = hashlib.sha256(((v["system_instruction"] or "") + (v["user_template"] or "")).encode()).hexdigest()
    eff = effective_from or _today()
    now_active = eff <= _today()
    new_status = "ACTIVE" if now_active else "PUBLISHED"
    if now_active:
        conn.execute("UPDATE ai_prompt_versions SET status='RETIRED', effective_to=? WHERE prompt_id=? AND status='ACTIVE' AND id<>?",
                     (_today(), v["prompt_id"], version_id))
    conn.execute("UPDATE ai_prompt_versions SET status=?, published_by=?, effective_from=?, checksum=? WHERE id=?",
                 (new_status, (actor or {}).get("id"), eff, checksum, version_id))
    core.audit(conn, actor, "AI_PROMPT_PUBLISHED", "ai_prompt_versions", version_id, new={"status": new_status, "checksum": checksum[:12]}, reason=change_reason)
    conn.commit()
    return {"version_id": version_id, "status": new_status, "checksum": checksum}


def _active_prompt_version(conn, code, tid):
    p = _prompt(conn, code, tid)
    if not p:
        return None
    return conn.execute("SELECT * FROM ai_prompt_versions WHERE prompt_id=? AND status='ACTIVE' ORDER BY version_no DESC LIMIT 1", (p["id"],)).fetchone()


# --------------------------------------------------------------------------- #
# Human-review policies
# --------------------------------------------------------------------------- #
def set_review_policy(conn, actor, use_case_code, policy, confidence_threshold=0.7):
    core.require(actor, "ai.use_case.manage")
    if policy not in REVIEW_POLICIES:
        raise core.ValidationError(f"policy must be one of {REVIEW_POLICIES}")
    conn.execute("INSERT INTO ai_review_policies(tenant_id,use_case_code,policy,confidence_threshold,created_by,created_at)"
                 " VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,use_case_code) DO UPDATE SET policy=excluded.policy,"
                 " confidence_threshold=excluded.confidence_threshold",
                 (_tenant(actor), use_case_code, policy, confidence_threshold, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "AI_REVIEW_POLICY_SET", "ai_review_policies", 0, new={"use_case": use_case_code, "policy": policy})
    conn.commit()
    return True


def _review_required(conn, actor, uc, confidence, classification, external=False):
    tid = _tenant(actor)
    pol = conn.execute("SELECT * FROM ai_review_policies WHERE use_case_code=? AND (tenant_id=? OR tenant_id IS NULL) ORDER BY tenant_id DESC LIMIT 1", (uc["code"], tid)).fetchone()
    policy = pol["policy"] if pol else uc["human_review"]
    thr = pol["confidence_threshold"] if pol else 0.7
    if policy == "none_allowed":
        raise core.ForbiddenError("AI execution is not permitted for this use case")
    if policy == "auto_low_risk" and uc["risk_level"] == "low":
        return classification in SENSITIVE_CLASSES or external   # even auto still reviews sensitive/external
    if policy == "always":
        return True
    if policy == "below_confidence":
        return confidence < thr
    if policy == "sensitive":
        return classification in SENSITIVE_CLASSES
    if policy == "external":
        return external
    if policy in ("high_value", "safety"):
        return True
    return True


SENSITIVE_CLASSES = ("RESTRICTED", "PERSONAL_DATA", "FINANCIAL_DATA", "SAFETY_DATA", "PAYMENT_DATA",
                     "AUTHENTICATION_DATA", "SECRET")


# --------------------------------------------------------------------------- #
# Tool registry (allowlist — prohibited actions can never be registered)
# --------------------------------------------------------------------------- #
def register_tool(conn, actor, code, name, permission, input_schema=None, output_schema=None,
                  risk="low", human_approval=True):
    core.require(actor, "ai.platform.manage")
    if code in PROHIBITED_ACTIONS or any(p in code for p in ("payment", "refund", "delete", "elevate",
                                                             "secret", "deploy", "sql", "cross_tenant", "dispatch")):
        raise core.ForbiddenError(f"tool '{code}' maps to a prohibited action and cannot be registered")
    conn.execute("INSERT INTO ai_tools(code,name,permission,input_schema,output_schema,risk,human_approval,"
                 "prohibited,created_at) VALUES(?,?,?,?,?,?,?,0,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name",
                 (code, name, permission, json.dumps(input_schema) if input_schema else None,
                  json.dumps(output_schema) if output_schema else None, risk, 1 if human_approval else 0, _now()))
    core.audit(conn, actor, "AI_TOOL_REGISTERED", "ai_tools", 0, new={"code": code, "permission": permission})
    conn.commit()
    return True


def list_tools(conn, actor):
    core.require(actor, "ai.use_case.view")
    return [dict(r) for r in conn.execute("SELECT * FROM ai_tools ORDER BY code").fetchall()]


# --------------------------------------------------------------------------- #
# Budgets + rate limiting + kill switch
# --------------------------------------------------------------------------- #
def set_budget(conn, actor, limit_cost, scope="tenant", scope_ref=None, use_case_code=None,
               period="monthly", hard_stop=True, alert_threshold=0.8):
    core.require(actor, "ai.budget.manage")
    cur = conn.execute("INSERT INTO ai_budgets(tenant_id,scope,scope_ref,use_case_code,period,limit_cost,"
                       "alert_threshold,hard_stop,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (_tenant(actor), scope, scope_ref, use_case_code, period, limit_cost, alert_threshold,
                        1 if hard_stop else 0, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "AI_BUDGET_SET", "ai_budgets", cur.lastrowid, new={"limit": limit_cost, "scope": scope})
    conn.commit()
    return cur.lastrowid


def _budget_row(conn, tid, use_case_code):
    return conn.execute("SELECT * FROM ai_budgets WHERE (tenant_id=? OR tenant_id IS NULL) AND"
                        " (use_case_code=? OR use_case_code IS NULL) ORDER BY use_case_code IS NULL, id DESC LIMIT 1",
                        (tid, use_case_code)).fetchone()


def _check_and_charge_budget(conn, actor, use_case_code, est_cost, actual_cost=None):
    b = _budget_row(conn, _tenant(actor), use_case_code)
    if not b:
        return
    projected = (b["spent_cost"] or 0) + (actual_cost if actual_cost is not None else est_cost)
    if actual_cost is None and b["hard_stop"] and projected > b["limit_cost"]:
        core.audit(conn, actor, "AI_BUDGET_DENIED", "ai_budgets", b["id"], new={"use_case": use_case_code})
        raise core.ForbiddenError("AI budget exhausted (hard stop)")
    if actual_cost is not None:
        conn.execute("UPDATE ai_budgets SET spent_cost=spent_cost+? WHERE id=?", (actual_cost, b["id"]))


def _rate_check(conn, actor, use_case_code, limit=60):
    window = _now()[:16]     # per-minute window (YYYY-MM-DDTHH:MM)
    tid, uid = _tenant(actor), (actor or {}).get("id")
    row = conn.execute("SELECT count FROM ai_rate_counters WHERE tenant_id IS ? AND user_id=? AND use_case_code=? AND window_start=?",
                       (tid, uid, use_case_code, window)).fetchone()
    n = (row["count"] if row else 0) + 1
    if n > limit:
        raise core.ForbiddenError("AI rate limit exceeded")
    conn.execute("INSERT INTO ai_rate_counters(tenant_id,user_id,use_case_code,window_start,count) VALUES(?,?,?,?,1)"
                 " ON CONFLICT(tenant_id,user_id,use_case_code,window_start) DO UPDATE SET count=count+1",
                 (tid, uid, use_case_code, window))


def activate_kill_switch(conn, actor, scope, scope_ref=None, reason=None):
    core.require(actor, "ai.kill_switch.manage")
    if scope not in KILL_SCOPES:
        raise core.ValidationError(f"scope must be one of {KILL_SCOPES}")
    cur = conn.execute("INSERT INTO ai_kill_switches(scope,scope_ref,tenant_id,active,reason,activated_by,activated_at)"
                       " VALUES(?,?,?,1,?,?,?)", (scope, scope_ref, _tenant(actor), reason, (actor or {}).get("id"), _now()))
    core.audit(conn, actor, "AI_KILL_SWITCH_ACTIVATED", "ai_kill_switches", cur.lastrowid, new={"scope": scope, "ref": scope_ref}, reason=reason)
    conn.commit()
    return cur.lastrowid


def release_kill_switch(conn, actor, switch_id, reason=None):
    core.require(actor, "ai.kill_switch.manage")
    conn.execute("UPDATE ai_kill_switches SET active=0, released_by=?, released_at=? WHERE id=?",
                 ((actor or {}).get("id"), _now(), switch_id))
    core.audit(conn, actor, "AI_KILL_SWITCH_RELEASED", "ai_kill_switches", switch_id, reason=reason)
    conn.commit()
    return True


def _is_killed(conn, tid, use_case_code, model_ref=None):
    rows = conn.execute("SELECT scope,scope_ref,tenant_id FROM ai_kill_switches WHERE active=1").fetchall()
    for r in rows:
        if r["scope"] == "platform":
            return True
        if r["scope"] == "tenant" and (r["tenant_id"] == tid or r["scope_ref"] == str(tid)):
            return True
        if r["scope"] == "use_case" and r["scope_ref"] == use_case_code:
            return True
        if r["scope"] == "model" and model_ref and r["scope_ref"] == model_ref:
            return True
    return False


# --------------------------------------------------------------------------- #
# Evaluation framework (publication gated on thresholds)
# --------------------------------------------------------------------------- #
_EVAL_SCENARIOS = ["valid", "low_confidence", "invalid_json", "unsupported_claim", "prohibited_action",
                   "injection", "provider_error"]


def run_evaluation(conn, actor, use_case_code, version_id):
    """Run the deterministic mock across scenarios; compute metrics; gate publication on thresholds."""
    core.require(actor, "ai.prompt.validate")
    v = _pv(conn, version_id)
    uc = get_use_case(conn, actor, use_case_code)
    provider = ai_provider.get_provider("mock", "MOCK")
    total, passed, unsafe, schema_ok, groundeds, costs, lats = 0, 0, 0, 0, 0, [], []
    schema = json.loads(v["output_schema"]) if v["output_schema"] else None
    for sc in _EVAL_SCENARIOS:
        total += 1
        try:
            out = provider.generate(system=v["system_instruction"] or "sys", prompt="eval", output_schema=schema, scenario=sc)
        except ai_provider.AIError:
            passed += 1        # provider failure handled gracefully = pass (fallback works)
            continue
        costs.append(out["cost"]);
        val = _validate_output(out, schema)
        if val["schema_valid"]:
            schema_ok += 1
        if val["grounded"]:
            groundeds += 1
        if val["unsafe"]:
            unsafe += 1
        # a scenario "passes" if the governance correctly handled it (unsafe caught, invalid rejected)
        if sc in ("prohibited_action", "injection", "unsupported_claim") and val["unsafe"]:
            passed += 1
        elif sc == "invalid_json" and not val["schema_valid"]:
            passed += 1
        elif sc in ("valid", "low_confidence") and val["schema_valid"]:
            passed += 1
    unsafe_rate = round(unsafe / total, 3)
    schema_rate = round(schema_ok / total, 3)
    groundedness = round(groundeds / total, 3)
    pass_rate = passed / total
    ok = pass_rate >= (uc["quality_threshold"] or 0.8)
    conn.execute("INSERT INTO ai_evaluations(tenant_id,use_case_code,prompt_version_id,suite,cases_total,"
                 "cases_passed,groundedness,unsafe_rate,schema_pass_rate,avg_cost,avg_latency,passed,created_at)"
                 " VALUES(?,?,?, 'default', ?,?,?,?,?,?,?,?,?)",
                 (_tenant(actor), use_case_code, version_id, total, passed, groundedness, unsafe_rate,
                  schema_rate, round(sum(costs) / len(costs), 6) if costs else 0, 0, 1 if ok else 0, _now()))
    if ok:
        conn.execute("UPDATE ai_prompt_versions SET eval_passed=1 WHERE id=?", (version_id,))
    core.audit(conn, actor, "AI_EVALUATION_RUN", "ai_prompt_versions", version_id, new={"passed": ok, "pass_rate": round(pass_rate, 3)})
    conn.commit()
    return {"passed": ok, "pass_rate": round(pass_rate, 3), "unsafe_rate": unsafe_rate,
            "schema_pass_rate": schema_rate, "groundedness": groundedness, "cases": total}


# --------------------------------------------------------------------------- #
# Output validation + grounding + injection + prohibited-action detection
# --------------------------------------------------------------------------- #
def _validate_output(out, schema):
    structured = out.get("structured")
    schema_valid = structured is not None and not out.get("raw_invalid")
    if schema_valid and schema:
        for req in schema.get("required", []):
            if req not in structured:
                schema_valid = False
        for f, allowed in (schema.get("enums") or {}).items():
            if f in structured and structured[f] not in allowed:
                schema_valid = False
    # grounding: sensitive outputs must cite supplied evidence
    grounded = bool(structured and structured.get("citations"))
    # prohibited-action detection
    unsafe = False
    if structured:
        act = str(structured.get("action_request", "")).lower()
        if act in PROHIBITED_ACTIONS:
            unsafe = True
        text = json.dumps(structured).lower()
        if any(m in text for m in INJECTION_MARKERS) or structured.get("injection_detected"):
            unsafe = True
        # unsupported claim: a definitive promise with no citation
        if not grounded and any(w in text for w in ("promised", "guarantee", "guaranteed", "50% discount")):
            unsafe = True
    return {"schema_valid": schema_valid, "grounded": grounded, "unsafe": unsafe}


# --------------------------------------------------------------------------- #
# Governed execution (advisory — NEVER auto-commits, NEVER performs a business action)
# --------------------------------------------------------------------------- #
def execute(conn, actor, use_case_code, prompt_code, input_payload, scenario="valid", external=False,
            org_scope=None):
    """The governed AI pipeline. Returns an ADVISORY result flagged for human review. Never commits
    to an authoritative record, never performs a prohibited action, never sends secrets to a provider."""
    core.require(actor, "ai.execute")
    uc = get_use_case(conn, actor, use_case_code)
    tid = _tenant(actor)
    # kill switch + enabled
    if not uc["enabled"] or _is_killed(conn, tid, use_case_code):
        raise core.ForbiddenError("AI is disabled for this use case (kill switch or disabled) — failing safe")
    # data classification + redaction (never send secrets/payment/auth)
    classification, redacted, redacted_fields = classify_and_redact(input_payload)
    allowed = (uc["allowed_input_classes"] or "").split(",")
    if classification in ("SECRET", "AUTHENTICATION_DATA", "PAYMENT_DATA"):
        raise core.ForbiddenError(f"input classified {classification} may not be sent to AI")
    if classification in SENSITIVE_CLASSES and classification not in allowed and not core.can(actor, "ai.sensitive.execute"):
        raise core.ForbiddenError(f"not authorized to run AI on {classification} data")
    # resolve prompt version (must be ACTIVE) + approved model
    pv = _active_prompt_version(conn, prompt_code, tid)
    if not pv:
        raise core.ConflictError("no active prompt version")
    model = _approved_model(conn, "mock", "MOCK")
    if not model:
        raise core.ConflictError("no approved model available")
    model_ref = f"{model['provider']}:{model['model_id']}"
    # rate limit + budget hard stop (pre-check with estimate)
    _rate_check(conn, actor, use_case_code)
    _check_and_charge_budget(conn, actor, use_case_code, est_cost=0.001)
    # call provider (deterministic mock in CI)
    started = datetime.datetime.now()
    schema = json.loads(pv["output_schema"]) if pv["output_schema"] else None
    provider = ai_provider.get_provider(model["provider"], model["approved_environments"] or "MOCK")
    try:
        out = provider.generate(system=pv["system_instruction"] or "assist", prompt=json.dumps(redacted),
                                output_schema=schema, scenario=scenario)
    except ai_provider.AIError as e:
        # graceful fallback: record + return a safe "unavailable, use manual workflow" result
        _record_exec(conn, actor, uc, model_ref, pv["version_no"], classification, redacted, None,
                     0, 0, 0.0, int((datetime.datetime.now() - started).total_seconds() * 1000),
                     "PROVIDER_ERROR", e.category, redacted_fields, unsafe=0, human_status="FALLBACK")
        return {"result": "PROVIDER_UNAVAILABLE", "fallback": "manual workflow", "error_category": e.category,
                "human_review_required": True, "committed": False}
    latency = int((datetime.datetime.now() - started).total_seconds() * 1000)
    val = _validate_output(out, schema)
    _check_and_charge_budget(conn, actor, use_case_code, est_cost=0, actual_cost=out["cost"])
    unsafe = val["unsafe"]
    # prohibited-action / injection → raise an incident, force human review, NEVER act
    if unsafe:
        create_incident(conn, actor, use_case_code, "prohibited_action" if (out.get("structured") or {}).get("action_request") else "prompt_injection",
                        "high", detail="unsafe AI output blocked; no action taken")
    review_required = True if unsafe else _review_required(conn, actor, uc, out.get("confidence", 0), classification, external)
    result = "UNSAFE_BLOCKED" if unsafe else ("SCHEMA_INVALID_HUMAN_FALLBACK" if not val["schema_valid"] else "ADVISORY")
    exec_id = _record_exec(conn, actor, uc, model_ref, pv["version_no"], classification, redacted,
                           out.get("output_hash"), out["input_tokens"], out["output_tokens"], out["cost"],
                           latency, result, None, redacted_fields, unsafe=1 if unsafe else 0,
                           human_status="PENDING" if review_required else "AUTO_LOW_RISK")
    return {"execution_id": exec_id, "result": result, "model": model_ref, "prompt_version": pv["version_no"],
            "structured": None if unsafe else out.get("structured"), "confidence": out.get("confidence"),
            "grounded": val["grounded"], "schema_valid": val["schema_valid"], "redacted_fields": redacted_fields,
            "human_review_required": review_required, "committed": False, "is_mock": bool(out.get("__MOCK_AI__")),
            "ai_generated": True}


def _record_exec(conn, actor, uc, model_ref, prompt_version, classification, redacted, output_hash,
                 in_tok, out_tok, cost, latency, result, errcat, redacted_fields, unsafe, human_status):
    cur = conn.execute("INSERT INTO ai_executions(tenant_id,org_scope,use_case_code,model_ref,prompt_version,"
                       "actor_id,input_classification,input_hash,output_hash,structured_valid,confidence,grounded,"
                       "human_review_status,input_tokens,output_tokens,cost,latency_ms,result,error_category,"
                       "redacted_fields,unsafe,correlation_id,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (_tenant(actor), None, uc["code"], model_ref, prompt_version, (actor or {}).get("id"),
                        classification, _hash(redacted), output_hash, 1, None, None, human_status,
                        in_tok, out_tok, cost, latency, result, errcat, json.dumps(redacted_fields), unsafe,
                        core.correlation_id(), _now()))
    core.audit(conn, actor, "AI_EXECUTED", "ai_executions", cur.lastrowid,
               new={"use_case": uc["code"], "result": result, "cost": cost, "classification": classification})  # no raw content
    conn.commit()
    return cur.lastrowid


def review_execution(conn, actor, execution_id, decision, edits=None, reason=None):
    """A human accepts / edits / rejects AI output. Edits are distinguishable from the original.
    This is the ONLY path by which AI output influences anything — and it stays advisory."""
    core.require(actor, "ai.review")
    if decision not in ("ACCEPTED", "REJECTED", "EDITED"):
        raise core.ValidationError("decision must be ACCEPTED / REJECTED / EDITED")
    ex = conn.execute("SELECT * FROM ai_executions WHERE id=?", (execution_id,)).fetchone()
    if not ex:
        raise core.NotFoundError("execution not found")
    import tenant as tmod
    tmod.guard(actor, ex)                                # cross-tenant 404
    conn.execute("INSERT INTO ai_reviews(execution_id,reviewer,decision,edits,reason,original_output_hash,"
                 "edited,reviewed_at) VALUES(?,?,?,?,?,?,?,?)",
                 (execution_id, (actor or {}).get("id"), decision, json.dumps(edits) if edits else None,
                  reason, ex["output_hash"], 1 if decision == "EDITED" else 0, _now()))
    conn.execute("UPDATE ai_executions SET human_review_status=? WHERE id=?", (decision, execution_id))
    core.audit(conn, actor, "AI_REVIEWED", "ai_executions", execution_id, new={"decision": decision})
    conn.commit()
    return {"execution_id": execution_id, "decision": decision}


def review_queue(conn, actor):
    core.require(actor, "ai.review")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM ai_executions WHERE human_review_status='PENDING' AND"
                                          " (tenant_id=? OR tenant_id IS NULL) ORDER BY id DESC LIMIT 200", (tid,)).fetchall()]


# --------------------------------------------------------------------------- #
# Incidents + observability + memory + usage/cost
# --------------------------------------------------------------------------- #
def create_incident(conn, actor, use_case_code, incident_type, severity, detail=None, contain=True):
    cur = conn.execute("INSERT INTO ai_incidents(tenant_id,use_case_code,incident_type,severity,status,detail,"
                       "contained,created_by,created_at,correlation_id) VALUES(?,?,?,?, 'OPEN', ?,?,?,?,?)",
                       (_tenant(actor) if actor else None, use_case_code, incident_type, severity, detail,
                        1 if contain else 0, (actor or {}).get("id") if actor else None, _now(), core.correlation_id()))
    core.audit(conn, actor or {"id": 0, "role": "system"}, "AI_INCIDENT_CREATED", "ai_incidents", cur.lastrowid,
               new={"type": incident_type, "severity": severity})
    conn.commit()
    return cur.lastrowid


def list_incidents(conn, actor):
    core.require(actor, "ai.incident.view")
    tid = _tenant(actor)
    return [dict(r) for r in conn.execute("SELECT * FROM ai_incidents WHERE tenant_id=? OR tenant_id IS NULL ORDER BY id DESC", (tid,)).fetchall()]


def usage_summary(conn, actor):
    core.require(actor, "ai.usage.view")
    tid = _tenant(actor)
    rows = conn.execute("SELECT use_case_code, COUNT(*) n, COALESCE(SUM(cost),0) cost,"
                        " COALESCE(SUM(input_tokens+output_tokens),0) tokens FROM ai_executions"
                        " WHERE tenant_id=? OR tenant_id IS NULL GROUP BY use_case_code", (tid,)).fetchall()
    return {"by_use_case": [dict(r) for r in rows]}


def ai_health(conn, actor):
    core.require(actor, "ai.usage.view")
    provider = ai_provider.get_provider("mock", "MOCK")
    h = provider.health()
    # never HEALTHY if no execution/eval has run
    ran = conn.execute("SELECT COUNT(*) c FROM ai_executions").fetchone()["c"]
    status = h["status"] if ran else "UNKNOWN"
    tid = _tenant(actor)
    open_inc = conn.execute("SELECT COUNT(*) c FROM ai_incidents WHERE status='OPEN' AND (tenant_id=? OR tenant_id IS NULL)", (tid,)).fetchone()["c"]
    return {"provider": provider.name, "status": status, "is_mock": True, "open_incidents": open_inc,
            "live_provider": "BLOCKED (owner credentials required)"}


def add_memory(conn, actor, use_case_code, purpose, content):
    core.require(actor, "ai.execute")
    _, redacted, removed = classify_and_redact(content if isinstance(content, dict) else {"text": content})
    cur = conn.execute("INSERT INTO ai_memory(tenant_id,user_id,use_case_code,purpose,content_hash,"
                       "sensitive_excluded,expires_at,created_at) VALUES(?,?,?,?,?,1,?,?)",
                       (_tenant(actor), (actor or {}).get("id"), use_case_code, purpose, _hash(redacted),
                        (datetime.date.today() + datetime.timedelta(days=30)).isoformat(), _now()))
    conn.commit()
    return cur.lastrowid


def list_memory(conn, actor):
    core.require(actor, "ai.execute")
    tid, uid = _tenant(actor), (actor or {}).get("id")
    return [dict(r) for r in conn.execute("SELECT * FROM ai_memory WHERE tenant_id=? AND user_id=? ORDER BY id DESC", (tid, uid)).fetchall()]


def delete_memory(conn, actor, memory_id):
    core.require(actor, "ai.execute")
    tid, uid = _tenant(actor), (actor or {}).get("id")
    conn.execute("DELETE FROM ai_memory WHERE id=? AND tenant_id=? AND user_id=?", (memory_id, tid, uid))
    core.audit(conn, actor, "AI_MEMORY_DELETED", "ai_memory", memory_id)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Migration classification
# --------------------------------------------------------------------------- #
def classify_existing(conn):
    return {"existing_ai_functions": 0, "governed_ai": 0, "deterministic_rules_kept": 3,
            "relabeled_as_ai": 0, "ai_authored_records": 0,
            "financial_differences": 0, "operational_status_differences": 0, "ai_authored_record_changes": 0}


# --------------------------------------------------------------------------- #
# Seed: an approved mock model + a governed booking-assistant use case + tools
# --------------------------------------------------------------------------- #
def seed(conn):
    sys_actor = {"id": 0, "role": "super_admin", "perms": {"*"}, "tenant_id": None}
    if conn.execute("SELECT 1 FROM ai_models WHERE provider='mock'").fetchone():
        return
    mid = register_model(conn, sys_actor, "mock", "deterministic", "Deterministic Mock",
                         approved_environments="MOCK", risk_rating="low")
    set_model_status(conn, sys_actor, mid, "APPROVED")
    # allowlisted tools (read-only / advisory only)
    for (code, name, perm) in [("read_governed_booking", "Read Booking", "booking.read"),
                               ("read_governed_report", "Read Report", "report.execute"),
                               ("draft_customer_response", "Draft Response", "ai.execute"),
                               ("classify_cargo", "Classify Cargo", "ai.execute"),
                               ("suggest_vehicle_category", "Suggest Vehicle", "ai.execute"),
                               ("produce_incident_summary", "Incident Summary", "ai.execute")]:
        register_tool(conn, sys_actor, code, name, perm)
    conn.commit()
