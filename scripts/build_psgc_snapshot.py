"""Build LiftHaul's browser-ready Philippine address snapshot.

Input is the hierarchical Second Quarter 2026 PSGC data mirrored by geoph-lite from
the Philippine Statistics Authority publication dated 30 June 2026. The generated
files are split by island group so the booking page downloads only the selected
part of the hierarchy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXPECTED = {"regions": 18, "provinces": 82, "localities": 1642, "barangays": 42010}

ISLAND_BY_REGION = {
    # Luzon
    "1300000000": "Luzon", "1400000000": "Luzon", "0100000000": "Luzon",
    "0200000000": "Luzon", "0300000000": "Luzon", "0400000000": "Luzon",
    "1700000000": "Luzon", "0500000000": "Luzon",
    # Visayas
    "0600000000": "Visayas", "1800000000": "Visayas", "0700000000": "Visayas",
    "0800000000": "Visayas",
    # Mindanao
    "0900000000": "Mindanao", "1000000000": "Mindanao", "1100000000": "Mindanao",
    "1200000000": "Mindanao", "1600000000": "Mindanao", "1900000000": "Mindanao",
}

MAP_SETTINGS = {
    "Luzon": {"center": [16.15, 121.0], "zoom": 6},
    "Visayas": {"center": [10.75, 123.65], "zoom": 7},
    "Mindanao": {"center": [7.65, 124.8], "zoom": 6},
}


def counts(regions: list[dict]) -> dict[str, int]:
    totals = {"regions": len(regions), "provinces": 0, "localities": 0, "barangays": 0}
    seen: set[str] = set()
    for region in regions:
        nodes = [region]
        totals["provinces"] += len(region["provinces"])
        direct = region["localities"]
        localities = list(direct)
        for province in region["provinces"]:
            nodes.append(province)
            localities.extend(province["localities"])
        totals["localities"] += len(localities)
        for locality in localities:
            nodes.append(locality)
            totals["barangays"] += len(locality["barangays"])
            nodes.extend(locality["barangays"])
        for node in nodes:
            code = str(node.get("psgc_code", ""))
            if not (len(code) == 10 and code.isdigit()):
                raise ValueError(f"Invalid PSGC code: {code!r}")
            if code in seen:
                raise ValueError(f"Duplicate PSGC code: {code}")
            seen.add(code)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/psgc"))
    args = parser.parse_args()

    source_bytes = args.source.read_bytes()
    dataset = json.loads(source_bytes.decode("utf-8"))
    regions = dataset.get("regions")
    if not isinstance(regions, list):
        raise ValueError("Source must contain a regions array")
    actual = counts(regions)
    if actual != EXPECTED:
        raise ValueError(f"Unexpected PSGC totals: {actual}; expected {EXPECTED}")
    missing = set(ISLAND_BY_REGION) ^ {r["psgc_code"] for r in regions}
    if missing:
        raise ValueError(f"Island mapping does not match the 18 regions: {sorted(missing)}")

    args.output.mkdir(parents=True, exist_ok=True)
    island_entries = []
    for island in ("Luzon", "Visayas", "Mindanao"):
        subset = [r for r in regions if ISLAND_BY_REGION[r["psgc_code"]] == island]
        filename = f"{island.lower()}.json"
        payload = {"island_group": island, "regions": subset}
        (args.output / filename).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        island_entries.append({
            "id": island,
            "file": f"data/psgc/{filename}",
            "regions": len(subset),
            **MAP_SETTINGS[island],
        })

    manifest = {
        "meta": {
            "name": "Philippine Standard Geographic Code",
            "as_of": "2026-06-30",
            "release": "Second Quarter 2026",
            "publisher": "Philippine Statistics Authority",
            "source": "https://psa.gov.ph/classification/psgc/",
            "license": "CC BY 4.0",
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "totals": actual,
        },
        "island_groups": island_entries,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "Built PSGC snapshot: "
        f"{actual['regions']} regions, {actual['provinces']} provinces, "
        f"{actual['localities']} cities/municipalities, {actual['barangays']} barangays"
    )


if __name__ == "__main__":
    main()
