#!/usr/bin/env python3
"""Deploy one exact connected-repository commit to Railway.

Uses Railway's documented serviceInstanceDeployV2 mutation. No implicit latest-
branch deploy is permitted. The project-scoped token is sent only to Railway.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://backboard.railway.com/graphql/v2"
MUTATION = """
mutation serviceInstanceDeployV2($serviceId: String!, $environmentId: String!, $commitSha: String!) {
  serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId, commitSha: $commitSha)
}
"""


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def main():
    token = required("RAILWAY_PROJECT_TOKEN")
    service_id = required("RAILWAY_SERVICE_ID")
    environment_id = required("RAILWAY_ENVIRONMENT_ID")
    commit_sha = required("RELEASE_SHA").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise SystemExit("RELEASE_SHA must be a full 40-character Git commit SHA")

    payload = json.dumps({
        "query": MUTATION,
        "variables": {
            "serviceId": service_id,
            "environmentId": environment_id,
            "commitSha": commit_sha,
        },
    }).encode()
    request = urllib.request.Request(ENDPOINT, data=payload, method="POST", headers={
        "Project-Access-Token": token,
        "Content-Type": "application/json",
        "User-Agent": "LiftHaul-Exact-Release/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Railway deploy request failed with HTTP {exc.code}") from exc
    if result.get("errors"):
        messages = "; ".join(str(item.get("message", "unknown GraphQL error")) for item in result["errors"])
        raise SystemExit(f"Railway rejected the exact-commit deploy: {messages}")
    deployment_id = (result.get("data") or {}).get("serviceInstanceDeployV2")
    if not deployment_id:
        raise SystemExit("Railway did not return a deployment id")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"deployment_id={deployment_id}\n")
    print(json.dumps({"release_sha": commit_sha, "deployment_id": deployment_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

