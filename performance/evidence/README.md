# Local performance evidence — 2026-09-07

These JSON files were produced by `performance/local_http_probe.py` against one verified local
LiftHaul API process on loopback with a disposable SQLite database. They are committed so the
percentile and error claims in the report can be independently inspected.

| File | Purpose | Gate |
|---|---|---|
| `read-clean.json` | 1, 25, 100, and 500 concurrent-client read stages | Pass under provisional p95/error thresholds; 500 has warnings |
| `read-clean-stress-1000.json` | 1,000 concurrent-client read stress | Fail |
| `mixed-100-v2.json` | Mandated mixed workload at 100 concurrent clients | Fail |
| `idempotency-20-v2.json` | 20 same-key booking retries on one process | Pass, but not cross-process proof |

`v2` means the harness result was corrected and rerun after removing contaminated local listener
processes. Temporary databases and server logs are intentionally excluded.

The local host is not production parity. Do not use these numbers for customer commitments or
infrastructure sizing.
