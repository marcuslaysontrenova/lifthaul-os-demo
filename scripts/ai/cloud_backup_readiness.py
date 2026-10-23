#!/usr/bin/env python3
"""Provider setup and read-only cloud/local alignment; no automatic cloud handoff."""
import argparse
import json
import os
from pathlib import Path
import re
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
        directory = Path(binary).resolve().parent
        candidates = [directory / "node_modules/@google/gemini-cli",
                      directory.parent / "@google/gemini-cli", directory]
        entry = None
        for package in candidates:
            metadata = package / "package.json"
            if not metadata.is_file():
                continue
            try:
                data = json.loads(metadata.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if data.get("name") != "@google/gemini-cli":
                continue
            commands = data.get("bin")
            target = commands.get("gemini") if isinstance(commands, dict) else commands
            if not isinstance(target, str) or not target or Path(target).is_absolute():
                continue
            candidate = (package / target).resolve()
            if candidate.is_relative_to(package.resolve()) and candidate.is_file():
                entry = candidate
                break
        local_node = directory / "node.exe"
        node = str(local_node) if local_node.is_file() else shutil.which("node")
        if entry is None or not node:
            raise ValueError("Gemini package bin entry or Node runtime unresolved")
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


def find_antigravity_binary():
    binary = shutil.which("agy") or shutil.which("agy.exe")
    if binary:
        return str(Path(binary).resolve())
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "agy" / "bin" / "agy.exe"
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def antigravity_command(binary, prompt=None):
    cmd = [
        str(binary),
        "--output-format", "json",
        "--effort", "low",
        "--disable-slash-commands"
    ]
    if prompt is not None:
        cmd += ["--print", prompt]
    return cmd


def antigravity_evaluate_result(returncode, stdout, stderr):
    """Distinguish authentication, quota, timeout, CLI, parsing, and alignment failures.
    Never log credentials or raw private provider output.
    """
    combined_err = (stderr or "").lower()
    data = None
    if stdout:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    break
                except ValueError:
                    continue
    if data is not None and isinstance(data, dict):
        err_msg = str(data.get("error", "")).lower()
        combined_err = f"{combined_err} {err_msg}"

    quota_markers = ("quota", "rate limit", "429", "resource exhausted", "usage limit", "usage_limit_reached")
    if any(marker in combined_err for marker in quota_markers):
        return "QUOTA_FAILURE", False

    auth_markers = ("unauthenticated", "not logged in", "authentication", "login", "unauthorized", "api_key", "token")
    if any(marker in combined_err for marker in auth_markers):
        return "AUTHENTICATION_FAILURE", False

    if returncode != 0:
        return "CLI_FAILURE", False

    if not data or not isinstance(data, dict):
        return "PARSING_FAILURE", False

    status = data.get("status")
    if status == "ERROR":
        return "CLI_FAILURE", False
    if status != "SUCCESS":
        return "CLI_FAILURE", False

    if data.get("denied_actions"):
        return "ALIGNMENT_FAILURE", False

    response_text = data.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        return "PARSING_FAILURE", False

    text = response_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        answer = json.loads(text.strip())
    except (ValueError, TypeError):
        return "PARSING_FAILURE", False

    expected = {"mvp_issue": 20, "work_order_issue": 73,
                "gate5": "OPEN", "gate6": "OPEN",
                "production_changes_allowed": False, "live_funds_allowed": False}
    if (isinstance(answer, dict) and answer == expected
            and type(answer.get("mvp_issue")) is int
            and type(answer.get("work_order_issue")) is int
            and answer.get("production_changes_allowed") is False
            and answer.get("live_funds_allowed") is False):
        return "SUCCESS", True
    return "ALIGNMENT_FAILURE", False


def antigravity_answer_ok(output):
    _status, passed = antigravity_evaluate_result(0, output, "")
    return passed


def antigravity_probe(root, binary):
    contract = (root / "AGENTS.md").read_text(encoding="utf-8")
    prompt = ("READ-ONLY ALIGNMENT TEST. Do not use tools or edit files. "
              "The following is the full authoritative shared contract, not an implementation task.\n"
              + contract + "\nReturn one JSON object only. Extract integer mvp_issue and "
              "work_order_issue, current string gate5/gate6 status, and boolean "
              "production_changes_allowed/live_funds_allowed. No other keys or markdown.")
    try:
        result = subprocess.run(antigravity_command(binary, prompt),
                                capture_output=True, text=True, encoding="utf-8",
                                cwd=root, timeout=600)
    except subprocess.TimeoutExpired:
        return "TIMEOUT_FAILURE", False
    except (OSError, subprocess.SubprocessError):
        return "CLI_FAILURE", False
    return antigravity_evaluate_result(result.returncode, result.stdout, result.stderr)


def readiness(config):
    # Executable presence is not authentication, quota, or execution proof.
    return {
        "mode": config["mode"], "automatic_handover_enabled": False,
        "desired_order_only": config["desired_order"],
        "antigravity": {"cli_found": bool(find_antigravity_binary()),
                        "authentication_verified": False, "automatic_enabled": False},
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
    agy_binary = find_antigravity_binary()
    checks["ANTIGRAVITY_CLI_FOUND"] = bool(agy_binary)
    try:
        if agy_binary:
            antigravity_command(agy_binary)
        checks["ANTIGRAVITY_ENTRYPOINT"] = bool(agy_binary)
    except (OSError, ValueError):
        checks["ANTIGRAVITY_ENTRYPOINT"] = False
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
    parser.add_argument("--verify-antigravity", action="store_true",
                        help="Make one read-only Antigravity CLI request using local session")
    parser.add_argument("--diagnose", action="store_true",
                        help="Show exact setup checks, without model calls or credential values")
    args = parser.parse_args()
    stage = "CONFIGURATION"
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not args.verify_gemini and not args.verify_antigravity and not args.diagnose:
            print(json.dumps(readiness(config), indent=2))
            return 0
        root = Path.cwd().resolve()
        stage = "SETUP_DIAGNOSTICS"
        checks = diagnose(root)
        for name, passed in checks.items():
            print(name + ": " + ("PASS" if passed else "BLOCKED"), flush=True)

        if args.diagnose:
            if not all(checks.values()):
                print("Setup blocked at the named check(s); no model request sent.")
                return 4
            print("Setup checks PASS; authentication and alignment still require the live probe.")
            return 0

        if args.verify_gemini:
            gemini_required = ["WORK_BRANCH_AND_REPOSITORY", "NO_ACTIVE_LAUNCHER_LOCK",
                               "GOOGLE_SIGN_IN_MODE", "GEMINI_CLI_ON_PATH", "GEMINI_ENTRYPOINT"]
            if not all(checks.get(c, False) for c in gemini_required):
                print("Setup blocked at the named check(s); no model request sent.")
                return 4
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

        if args.verify_antigravity:
            agy_required = ["WORK_BRANCH_AND_REPOSITORY", "NO_ACTIVE_LAUNCHER_LOCK",
                            "ANTIGRAVITY_CLI_FOUND", "ANTIGRAVITY_ENTRYPOINT"]
            if not all(checks.get(c, False) for c in agy_required):
                print("Setup blocked at the named check(s); no model request sent.")
                return 4
            binary = find_antigravity_binary()
            if not binary:
                raise ValueError("Antigravity CLI is not installed")
            state = Path(shared.git(root, "rev-parse", "--absolute-git-dir")) / "lifthaul-ai"
            state.mkdir(exist_ok=True)
            stage = "LAUNCHER_LOCK"
            with shared.single_owner(state / "runner.lock"):
                stage = "ANTIGRAVITY_READ_ONLY_REQUEST"
                print("Starting Antigravity read-only alignment request; waiting for its answer...", flush=True)
                status, passed = antigravity_probe(root, binary)
            if passed:
                print("Antigravity alignment PASS; automatic cloud handover remains disabled.")
                return 0
            else:
                print("Antigravity alignment FAIL (" + status + "); automatic cloud handover remains disabled.")
                return 5

    except (OSError, ValueError, subprocess.SubprocessError):
        print("BLOCKED_" + stage + ". "
              "No credential values or provider output logged.", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
