"""Fail closed on owner or public surface failure; no network requests here."""
import os
import sys

def evaluate(env):
    owner = env.get("OWNERSHIP_OUTCOME", "")
    surface = env.get("SURFACE_OUTCOME", "")
    return {"ownership_passed": owner == "success", "surface_passed": surface == "success",
            "monitor_passed": owner == "success" and surface == "success"}

def main():
    report = evaluate(os.environ)
    print("Monitor gate: ownership=" + ("PASS" if report["ownership_passed"] else "FAIL")
          + ", public_surface=" + ("PASS" if report["surface_passed"] else "FAIL"))
    return 0 if report["monitor_passed"] else 1

if __name__ == "__main__":
    sys.exit(main())
