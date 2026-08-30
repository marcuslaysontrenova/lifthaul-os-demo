# Philippine location data

The JSON files in this directory are generated from the Philippine Statistics Authority (PSA)
Philippine Standard Geographic Code as of **30 June 2026** (Second Quarter 2026).

- Official source: https://psa.gov.ph/classification/psgc/
- Source mirror used for the reproducible build: https://github.com/ianlabicani/geoph-lite
- PSGC data license: Creative Commons Attribution 4.0 International (CC BY 4.0)
- Build command: `python scripts/build_psgc_snapshot.py <psgc-2q-2026-hierarchical.json>`

The mirror preserves official region-level parentage for NCR, independent/highly urbanized cities,
Pateros, and BARMM municipalities. The LiftHaul interface labels those entries as independent or
region-level areas instead of inventing a province.
