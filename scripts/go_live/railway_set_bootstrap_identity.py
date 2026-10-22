#!/usr/bin/env python3
"""Synchronize LiftHaul's protected bootstrap identity through Railway's API.

The script deliberately accepts secrets only through the process environment and
never writes or prints their values. Railway's project-scoped token is sent with
the same Project-Access-Token header used by the exact-release deployment path.
"""

import json
import os
import sys
import urllib.error
import urllib.request


ENDPOINT = "https://backboard.railway.com/graphql/v2"
MUTATION = """
mutation variableCollectionUpsert($input: VariableCollectionUpsertInput!) {
  variableCollectionUpsert(input: $input)
}
"""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def main() -> int:
    token = required("RAILWAY_PROJECT_TOKEN")
    project_id = required("RAILWAY_PROJECT_ID")
    environment_id = required("RAILWAY_ENVIRONMENT_ID")
    service_id = required("RAILWAY_SERVICE_ID")
    email = required("PROVISION_ADMIN_EMAIL")
    password = required("PROVISION_ADMIN_PASSWORD")

    if not email.endswith("@qa.lifthaul.com.ph"):
        raise SystemExit("provisioning email must use the controlled QA domain")
    if len(password) < 24:
        raise SystemExit("provisioning password does not meet the production minimum")

    payload = json.dumps({
        "query": MUTATION,
        "variables": {
            "input": {
                "projectId": project_id,
                "environmentId": environment_id,
                "serviceId": service_id,
                "variables": {
                    "LH_ADMIN_EMAIL": email,
                    "LH_ADMIN_PASSWORD": password,
                },
            },
        },
    }).encode()
    request = urllib.request.Request(ENDPOINT, data=payload, method="POST", headers={
        "Project-Access-Token": token,
        "Content-Type": "application/json",
        "User-Agent": "LiftHaul-Gate5-Bootstrap-Sync/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Railway variable synchronization failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit("Railway variable synchronization could not reach the API") from exc

    if result.get("errors"):
        messages = "; ".join(str(item.get("message", "unknown GraphQL error")) for item in result["errors"])
        raise SystemExit(f"Railway rejected the variable synchronization: {messages}")
    applied = (result.get("data") or {}).get("variableCollectionUpsert")
    if applied is not True:
        raise SystemExit("Railway did not confirm the variable synchronization")

    print(json.dumps({
        "result": "accepted",
        "variable_names": ["LH_ADMIN_EMAIL", "LH_ADMIN_PASSWORD"],
        "secret_values_recorded": False,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
