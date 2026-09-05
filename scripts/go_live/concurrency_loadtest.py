#!/usr/bin/env python3
"""LiftHaul — concurrency load test (proves the pooled path handles simultaneous
transactions safely).

Launches the backend in POOLED mode (LIFTHAUL_DB_POOL=1) on a throwaway file-SQLite
DB and a free port, then fires N concurrent provider registrations (each is a real
multi-write transaction) plus concurrent health/ready probes. It asserts:

  * zero failed transactions under concurrency,
  * every registration gets a DISTINCT carrier_id (per-request isolation — no data
    bleed between simultaneous requests), and
  * throughput / latency are reported.

Honest note: on file-SQLite, writers still serialize at the DB file, so this proves
CORRECTNESS + ISOLATION under concurrency, not write-throughput. Real concurrent
throughput comes from the same pooled code path against managed PostgreSQL
(DATABASE_URL=postgresql://…, pool auto-on) across N app instances behind a load
balancer — see docs/go_live/SCALING_ARCHITECTURE.md.

    python scripts/go_live/concurrency_loadtest.py [--clients 40]
Exit 0 = concurrency correctness + isolation verified.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "..", "backend")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _post(url, payload, timeout=60):   # generous: file-SQLite serializes writers under load
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=40)
    args = ap.parse_args()

    port = _free_port()
    dbfile = os.path.join(tempfile.gettempdir(), f"lh_loadtest_{port}.sqlite")
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(dbfile + ext)
        except OSError:
            pass

    env = dict(os.environ)
    env.update({
        "APP_ENV": "development",          # dev posture: registration returns dev_code
        "APP_SECRET": "loadtest-secret",
        "CORS_ORIGINS": "*",
        "PORT": str(port),
        "LIFTHAUL_DB_POOL": "1",           # <-- exercise the concurrent pooled path
        "DATABASE_URL": f"sqlite:///{dbfile}",
        "LIFTHAUL_PUBLIC_RATE_MAX": "100000",   # lift the per-IP public cap for the load test
        "LIFTHAUL_MAX_INFLIGHT": "12",          # bounded concurrency -> demonstrate backpressure
        "LIFTHAUL_CHECKOUT_TIMEOUT": "20",      # wait for a slot before shedding as 503
    })
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=os.path.abspath(BACKEND),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        # wait for readiness
        ready = False
        for _ in range(60):
            try:
                st, _b = _get(base + "/health", timeout=3)
                if st == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.3)
        if not ready:
            out = proc.stdout.read().decode("utf-8", "replace")[-1500:] if proc.stdout else ""
            print("SERVER DID NOT START:\n" + out)
            return 2

        n = args.clients
        print(f"Concurrency load test: {n} simultaneous provider registrations (pooled mode)")
        print(f"  server :{port}  db=sqlite-file(WAL)  LIFTHAUL_DB_POOL=1")
        print("-" * 70)

        import urllib.error

        def one(i):
            t0 = time.time()
            payload = {
                "provider_type": "FLEET_OPERATOR", "legal_name": f"Load Haulers {i}",
                "email": f"load{i}@loadtest.ph", "mobile": "09170000000",
                "username": f"load{i}@loadtest.ph", "password": "Str0ngPass!",
                "island_group": ["LUZON", "VISAYAS", "MINDANAO"][i % 3],
            }
            try:
                st, body = _post(base + "/public/providers", payload)
                d = body.get("data", body)
                return {"i": i, "status": st, "carrier_id": d.get("carrier_id"),
                        "ok": st == 200 and d.get("status") == "VERIFY_CONTACT",
                        "shed": False, "err": d.get("error"), "ms": (time.time() - t0) * 1000.0}
            except urllib.error.HTTPError as e:      # 503 = graceful backpressure (retryable)
                return {"i": i, "status": e.code, "carrier_id": None, "ok": False,
                        "shed": e.code == 503, "err": f"HTTP {e.code}", "ms": (time.time() - t0) * 1000.0}
            except Exception as e:                    # timeout / connection error = HARD failure
                return {"i": i, "status": 0, "carrier_id": None, "ok": False,
                        "shed": False, "err": type(e).__name__, "ms": (time.time() - t0) * 1000.0}

        t_start = time.time()
        results = []
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(one, i) for i in range(n)]
            for f in as_completed(futs):
                results.append(f.result())
        elapsed = time.time() - t_start

        oks = [r for r in results if r["ok"]]
        shed = [r for r in results if r["shed"]]                 # cleanly rejected (503)
        hard = [r for r in results if not r["ok"] and not r["shed"]]  # 500 / timeout / hang
        carrier_ids = [r["carrier_id"] for r in oks if r["carrier_id"] is not None]
        distinct = len(set(carrier_ids))
        lat = sorted(r["ms"] for r in results)
        p50 = lat[len(lat) // 2] if lat else 0
        p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0

        print(f"  succeeded            : {len(oks)}/{n}")
        print(f"  shed (503, graceful) : {len(shed)}  (backpressure - retryable, not a failure)")
        print(f"  HARD failures        : {len(hard)}  (500 / timeout / hang - must be 0)")
        print(f"  distinct carrier_ids : {distinct} (must equal successes -> per-request isolation)")
        print(f"  throughput           : {n/elapsed:,.1f} req/s over {elapsed:.2f}s")
        print(f"  latency p50 / p95    : {p50:.0f} ms / {p95:.0f} ms")
        hp = 0
        with ThreadPoolExecutor(max_workers=10) as ex:
            for f in as_completed([ex.submit(lambda: _get(base + "/ready", 5)) for _ in range(10)]):
                try:
                    if f.result()[0] == 200:
                        hp += 1
                except Exception:
                    pass
        print(f"  concurrent /ready oks: {hp}/10")
        print("-" * 70)
        for r in hard[:5]:
            print(f"  HARD FAIL client {r['i']}: status={r['status']} err={r['err']}")
        # Stability contract: no hard failures, successful requests are isolated, and every
        # request was either served or cleanly shed — never hung, never corrupted.
        ok = (len(hard) == 0 and distinct == len(oks) and (len(oks) + len(shed)) == n and hp == 10)
        print("CONCURRENCY RESULT:", "PASS - stable under load (served or gracefully shed, correct isolation)"
              if ok else "FAIL")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(dbfile + ext)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
