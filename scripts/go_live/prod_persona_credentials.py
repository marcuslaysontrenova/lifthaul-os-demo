#!/usr/bin/env python3
"""Derive repeatable Gate 5 synthetic credentials from one protected seed.

No credential is printed. Outputs are private runner files and must be removed by
the calling workflow. The seed belongs in the protected GitHub production
environment, never in source control or a workflow artifact.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

from prod_persona_uat import PERSONAS


def _digest(seed: bytes, label: str) -> bytes:
    return hmac.new(seed, ("lifthaul-gate5:" + label).encode(), hashlib.sha256).digest()


def derive(seed: str) -> tuple[dict, str, str]:
    if len(seed) < 32:
        raise ValueError("Gate 5 provisioning seed must contain at least 32 characters")
    raw = seed.encode("utf-8")

    def password(label: str) -> str:
        token = base64.urlsafe_b64encode(_digest(raw, "password:" + label)).decode().rstrip("=")[:26]
        return "Lh!9" + token

    def suffix(label: str, length: int = 12) -> str:
        return _digest(raw, "identity:" + label).hex()[:length]

    manifest = {}
    for persona in PERSONAS:
        safe = persona.replace("_", "-")
        manifest[persona] = {
            "email": f"gate5-{safe}-{suffix(persona)}@example.invalid",
            "password": password(persona),
            "marker": f"LH-G5-{safe.upper()}-{suffix('marker:' + persona, 10).upper()}",
        }
    # Production startup rejects reserved .invalid bootstrap addresses. This
    # controlled address is an identity label only; the workflow never sends mail.
    return manifest, f"gate5-bootstrap-{suffix('bootstrap')}@qa.lifthaul.com.ph", password("bootstrap")


def _private_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-out", required=True, type=Path)
    parser.add_argument("--admin-email-out", required=True, type=Path)
    parser.add_argument("--admin-password-out", required=True, type=Path)
    args = parser.parse_args()
    manifest, admin_email, admin_password = derive(os.environ.get("LH_PROD_PERSONA_PROVISION_SEED", ""))
    _private_write(args.manifest_out, json.dumps(manifest, separators=(",", ":")))
    _private_write(args.admin_email_out, admin_email)
    _private_write(args.admin_password_out, admin_password)
    print("Derived private Gate 5 credential files for 11 synthetic personas and one provisioning identity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
