"""Fail-closed evaluation of LiftHaul Codex client-confidence acceptance records.

This offline script never contacts production, invokes models or authorizes release.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
STATUSES = {"OPEN", "IN_PROGRESS", "DEVELOPMENT_PASS", "HOSTED_PASS", "HUMAN_APPROVED", "BLOCKED"}
PROOF = {"HOSTED_PASS", "HUMAN_APPROVED"}


def evaluate(register):
    if register.get("schema") != "lifthaul.client-confidence-ledger.v1":
        raise ValueError("Unsupported ledger")
    gates = register.get("gates", [])
    tasks = register.get("tasks", [])
    if len(gates) != 9 or [x.get("id") for x in gates] != list(range(1, 10)):
        raise ValueError("Exactly nine ordered gates required")
    if len(tasks) != 30 or [x.get("id") for x in tasks] != [f"T{i:02d}" for i in range(1, 31)]:
        raise ValueError("Exactly 30 ordered tasks required")
    source = register.get("source", {})
    sha = source.get("candidate_sha", "")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("Exact candidate SHA required")
    if register.get("recorded_historical_gate_count") != 3:
        raise ValueError("Historical baseline must be reconciled by human review")
    full_tasks = []
    for t in tasks:
        state = t.get("status")
        if state not in STATUSES:
            raise ValueError("Unknown task status")
        if state in PROOF:
            if not t.get("evidence_url") or t.get("evidence_sha") != sha or not t.get("accepted_by") or not t.get("accepted_at"):
                raise ValueError("Task acceptance must cite exact source, reviewer and evidence")
            full_tasks.append(t["id"])
    confirmed = 0
    for g in gates:
        state = g.get("status")
        if state not in ("OPEN", "RECORDED_HISTORICAL_PASS", "COMPLETE"):
            raise ValueError("Unknown gate status")
        if state == "COMPLETE":
            if not g.get("evidence_urls") or g.get("verified_candidate_sha") != sha or not g.get("verified_deployment_id") or not g.get("reviewed_by") or not g.get("reviewed_at"):
                raise ValueError("Completed gate requires signed candidate-specific hosted evidence")
            confirmed += 1
        if state == "RECORDED_HISTORICAL_PASS" and g.get("verified_candidate_sha"):
            raise ValueError("Historical evidence cannot claim current candidate verification")
    review = register.get("review", {})
    release = register.get("release", {})
    money = register.get("money", {})
    approved = (
        review.get("approved_candidate_sha") == sha and bool(review.get("independent_reviewer")) and bool(review.get("review_evidence_url"))
        and release.get("authorized_candidate_sha") == sha and bool(release.get("approved_by")) and bool(release.get("approval_evidence_url"))
    )
    limited_funds = (money.get("status") == "UNAUTHORIZED_NO_LIVE_FUNDS"
                     and money.get("current_candidate_off_verified") is True
                     and money.get("scope_exclusion_status") == "SIGNED"
                     and bool(money.get("signed_by")) and bool(money.get("evidence_url")))
    ready = (confirmed == 9 and len(full_tasks) == 30 and approved and limited_funds
             and register.get("production", {}).get("current_candidate_deployed") is True)
    if release.get("decision") == "GO" and not ready:
        raise ValueError("GO contradicts required verified evidence")
    return {"candidate_sha": sha, "historical_gates": 3, "current_candidate_gates_verified": confirmed,
            "tasks_with_complete_evidence": len(full_tasks), "pilot_target_tasks_remaining": 24-sum(x in full_tasks for x in [f"T{i:02d}" for i in range(1,25)]),
            "high_confidence_tasks_remaining": 30-len(full_tasks), "independent_approval_verified": bool(approved),
            "no_live_funds_scope_verified": bool(limited_funds), "mvp_go": bool(ready and release.get("decision") == "GO"),
            "release_decision": release.get("decision")}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", default="docs/readiness/MVP_CODEX_CLIENT_CONFIDENCE_LEDGER.json")
    a = p.parse_args()
    data = json.loads(Path(a.ledger).read_text(encoding="utf-8"))
    report = evaluate(data)
    print(json.dumps(report, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
