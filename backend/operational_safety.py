"""Process-level live-operation interlock shared by enforcement and observation.

LH_LIVE_OPERATIONS must be explicitly 'off' or 'on'. Missing/invalid values
remain UNKNOWN and deny real operations. 'on' only removes this interlock;
existing financial, legal, certification and authorization gates still apply.
Local synthetic service tests remain possible, but can never certify live OFF.
No endpoint changes this policy. No provider credentials are inspected here.
"""
import datetime
import os
import re

import core

FIELDS = (
    "live_collection_enabled", "live_refunds_enabled", "live_payouts_enabled",
    "live_settlement_enabled", "live_escrow_disbursement_enabled",
    "live_dispatch_enabled", "automatic_assignment_enabled",
    "external_financial_provider_enabled",
)
LOCAL_ENVS = {"development", "dev", "local", "test", "testing"}
ADMIN_ROLES = {"admin", "super_admin", "super_platform_admin", "platform_admin", "security_admin"}


def state(env=None):
    env = os.environ if env is None else env
    # Local synthetic execution cannot constitute proof that live dispatch is off.
    if str(env.get("APP_ENV", "")).strip().lower() not in {"production", "prod", "staging", "stage"}:
        return None
    value = env.get("LH_LIVE_OPERATIONS")
    if value == "off":
        return False
    if value == "on":
        return True
    return None


def require(capability, *, synthetic_local=False):
    if capability not in FIELDS:
        raise core.ForbiddenError("unknown operational capability")
    if synthetic_local and os.getenv("APP_ENV", "development").strip().lower() in LOCAL_ENVS:
        return  # deterministic local service execution only; never network adapters
    if state() is not True:
        raise core.ForbiddenError("live operation disabled or unverified")


def status(actor, identity):
    if not actor or actor.get("role") not in ADMIN_ROLES:
        raise core.ForbiddenError("operational safety status requires an administrative or security role")
    value = state()
    sha = identity.get("release_sha", "")
    deployment = identity.get("deployment_id", "")
    valid_identity = (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha)
                      and isinstance(deployment, str) and deployment not in ("", "unknown"))
    result = "FAIL" if value is True else ("PASS" if value is False and valid_identity else "BLOCKED")
    return {"schema": "lifthaul.operational-safety.v1", "result": result,
            "capabilities": {key: value for key in FIELDS},
            "enforcement": "shared_process_interlock_v1",
            "release_sha": sha if valid_identity else "unknown",
            "deployment_id": deployment if valid_identity else "unknown",
            "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
