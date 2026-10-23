#!/usr/bin/env python3
"""Provider setup and read-only Gemini alignment; no automatic cloud handoff."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ai import ollama_backup as shared

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "docs/ai/BACKUP_PROVIDERS.json"


def gemini_command(binary):
    prefix = [binary]
    if Path(binary).suffix.lower() in (".cmd", ".bat", ".ps1"):
        entry = Path(binary).parent / "node_modules/@google/gemini-cli/dist/index.js"
        node = shutil.which("node")
        if not entry.is_file() or not node:
            raise ValueError("Standard npm Gemini installation required")
        prefix = [node, str(entry)]
    return prefix + ["--approval-mode", "plan", "--output-format", "stream-json",
                     "--prompt", "Complete the read-only alignment task supplied on stdin. No tools."]


def gemini_answer_ok(output):
    chunks = []
    success = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            return False
        if not isinstance(event, dict):
            return False
        kind = event.get("type")
        if kind in ("error", "tool_use", "tool_result"):
            return False
        if kind == "message" and event.get("role") == "assistant":
            if not isinstance(event.get("content"), str):
                return False
            chunks.append(event["content"])
        if kind == "result":
            success = event.get("status") == "success" and not event.get("error")
    if not success or not chunks:
        return False
    normalized = json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": "".join(chunks)}})
    return shared.alignment_answer_ok(normalized)


def gemini_probe(root, binary):
    contract = (root / "AGENTS.md").read_text(encoding="utf-8")
    prompt = ("READ-ONLY ALIGNMENT TEST. Do not use tools or edit files. "
              "The following is the full authoritative shared contract, not an implementation task.\n"
              + contract + "\nReturn one JSON object only. Extract integer mvp_issue and "
              "work_order_issue, current string gate5/gate6 status, and boolean "
              "production_changes_allowed/live_funds_allowed. No other keys or markdown.")
    result = subprocess.run(gemini_command(binary), input=prompt, capture_output=True,
                            text=True, encoding="utf-8", cwd=root, timeout=600)
    return result.returncode == 0 and gemini_answer_ok(result.stdout)


def readiness(config):
    # Executable presence is not authentication, quota, or execution proof.
    return {
        "mode": config["mode"], "automatic_handover_enabled": False,
        "desired_order_only": config["desired_order"],
        "gemini": {"cli_found": bool(shutil.which("gemini")),
                   "authentication_verified": False, "automatic_enabled": False},
        "grok": {"adapter_implemented": False, "paid_calls_allowed": False,
                 "automatic_enabled": False},
        "note": "No provider or credential check performed; no API requests or billing actions."
    }


def diagnose(root):
    """Named setup checks only; never print credentials, paths or account data."""
    checks = {}
    checks["GOOGLE_SIGN_IN_MODE"] = not (
        any(os.environ.get(key) for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"))
        or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1"))
    try:
        shared.check_checkout(root)
        checks["WORK_BRANCH_AND_REPOSITORY"] = True
    except (OSError, ValueError, subprocess.SubprocessError):
        checks["WORK_BRANCH_AND_REPOSITORY"] = False
    binary = shutil.which("gemini")
    checks["GEMINI_CLI_ON_PATH"] = bool(binary)
    try:
        if binary:
            gemini_command(binary)
        checks["GEMINI_ENTRYPOINT"] = bool(binary)
    except (OSError, ValueError):
        checks["GEMINI_ENTRYPOINT"] = False
    try:
        state = Path(shared.git(root, "rev-parse", "--absolute-git-dir")) / "lifthaul-ai"
        checks["NO_ACTIVE_LAUNCHER_LOCK"] = not (state / "runner.lock").exists()
    except (OSError, ValueError, subprocess.SubprocessError):
        checks["NO_ACTIVE_LAUNCHER_LOCK"] = False
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-gemini", action="store_true",
                        help="Make one read-only Gemini request after local Google sign-in")
    parser.add_argument("--diagnose", action="store_true",
                        help="Show exact setup checks, without model calls or credential values")
    args = parser.parse_args()
    stage = "CONFIGURATION"
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not args.verify_gemini and not args.diagnose:
            print(json.dumps(readiness(config), indent=2))
            return 0
        root = Path.cwd().resolve()
        stage = "SETUP_DIAGNOSTICS"
        checks = diagnose(root)
        for name, passed in checks.items():
            print(name + ": " + ("PASS" if passed else "BLOCKED"), flush=True)
        if not all(checks.values()):
            print("Setup blocked at the named check(s); no model request sent.")
            return 4
        if args.diagnose:
            print("Setup checks PASS; authentication and alignment still require the live probe.")
            return 0
        binary = shutil.which("gemini")
        if not binary:
            raise ValueError("Gemini CLI is not installed")
        state = Path(shared.git(root, "rev-parse", "--absolute-git-dir")) / "lifthaul-ai"
        state.mkdir(exist_ok=True)
        stage = "LAUNCHER_LOCK"
        with shared.single_owner(state / "runner.lock"):
            stage = "GEMINI_READ_ONLY_REQUEST"
            print("Starting Gemini read-only alignment request; waiting for its answer...", flush=True)
            passed = gemini_probe(root, binary)
        print("Gemini alignment " + ("PASS" if passed else "FAIL")
              + "; automatic cloud handover remains disabled.")
        return 0 if passed else 5
    except (OSError, ValueError, subprocess.SubprocessError):
        print("BLOCKED_" + stage + ". "
              "No credential values or provider output logged.", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
