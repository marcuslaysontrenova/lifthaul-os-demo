#!/usr/bin/env python3
"""Zero-generation connectivity inventory and optional owner-approved, GET-only probe.

Does not execute AI models, run commands, create GitHub changes, or spend tokens.
Never prints API keys, returned response bodies, or provider error messages.
Default: offline inventory with no network activity.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", "models"),
    "claude": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models?limit=1", "data"),
    "grok": ("XAI_API_KEY", "https://api.x.ai/v1/models", "data"),
    "kimi": ("KIMI_API_KEY", "https://api.moonshot.ai/v1/models", "data"),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _access_headers(provider, key):
    headers = {"Accept": "application/json", "User-Agent": "LiftHaul-AI-ReadOnly-Connectivity/1.0"}
    if provider == "gemini":
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = "Bearer " + key
    if provider == "claude":
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _status(code):
    if code in (401, 403):
        return "BLOCKED_AUTH"
    if code == 402:
        return "BLOCKED_BILLING"
    if code == 429:
        return "BLOCKED_RATE_LIMIT"
    return "BLOCKED_PROVIDER_RESPONSE"


def inventory(provider, env=None):
    env = os.environ if env is None else env
    key_env, _, _ = PROVIDERS[provider]
    return {
        "provider": provider,
        "status": "CREDENTIAL_PRESENT_UNVERIFIED" if env.get(key_env) else "BLOCKED_NO_CREDENTIAL",
        "read_only": True,
        "models_generated": 0,
        "live_agent_connected": False,
    }


def probe(provider, env=None, opener=None):
    """One GET on a fixed HTTPS model-list endpoint; no generation or redirects."""
    env = os.environ if env is None else env
    result = inventory(provider, env)
    if result["status"] == "BLOCKED_NO_CREDENTIAL":
        return result
    if not (env.get("LH_AI_CONNECTIVITY_APPROVED") == "yes"
            and env.get("LH_AI_PROBE_ENV") == "development"
            and env.get("LH_AI_PROBE_KILL_SWITCH") == "off"):
        result["status"] = "BLOCKED_OWNER_APPROVAL_OR_KILL_SWITCH"
        return result
    key_env, url, list_key = PROVIDERS[provider]
    req = urllib.request.Request(url, method="GET", headers=_access_headers(provider, env[key_env]))
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=8) as response:
            code = response.status
            data = response.read(262145)
        if code != 200 or len(data) > 262144:
            result["status"] = _status(code)
            return result
        payload = json.loads(data.decode("utf-8"))
        models = payload.get(list_key) if isinstance(payload, dict) else None
        result["status"] = "VERIFIED_READ_ONLY" if isinstance(models, list) and models else "BLOCKED_UNEXPECTED_SCHEMA"
        result["model_entries_seen"] = len(models) if isinstance(models, list) else 0
    except urllib.error.HTTPError as error:
        result["status"] = _status(error.code)
    except (ValueError, UnicodeDecodeError, TimeoutError, OSError, urllib.error.URLError):
        result["status"] = "BLOCKED_UNVERIFIED_NETWORK"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=tuple(PROVIDERS) + ("all",), default="all")
    parser.add_argument("--mode", choices=("inventory", "probe"), default="inventory")
    args = parser.parse_args(argv)
    if args.mode == "probe" and args.provider == "all":
        parser.error("Probe exactly one provider per manually approved execution")
    names = tuple(PROVIDERS) if args.provider == "all" else (args.provider,)
    results = [inventory(name) if args.mode == "inventory" else probe(name) for name in names]
    print(json.dumps({"mode": args.mode, "results": results,
                      "automatic_handover_enabled": False}, sort_keys=True))
    return 0 if args.mode == "inventory" or results[0]["status"] == "VERIFIED_READ_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
