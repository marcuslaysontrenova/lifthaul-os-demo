"""RGO OS backend — database connection factory, migrations & schema versioning.

Selects the persistence backend from configuration (never hard-coded):
  * DATABASE_URL unset / sqlite  -> SQLite (dev, tests, single-node prod).
  * DATABASE_URL=postgres[ql]://  -> PostgreSQL (production system of record).

Schema is applied idempotently on connect (all module schemas) and stamped into a
`schema_version` table for versioning. The PostgreSQL path requires the `psycopg`
driver + a reachable server; when neither is present it raises a clear, honest
error (blocked on owner infrastructure) rather than pretending to connect.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

SCHEMA_VERSION = 20  # bump when any module schema changes (20 = Marketplace Inc.5: trip execution/GPS/geofence/POD)


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_version(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER, applied_at TEXT)")
    row = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    cur = row[0] if row else None
    if cur is None or cur < SCHEMA_VERSION:
        conn.execute("INSERT INTO schema_version(version,applied_at) VALUES(?,?)", (SCHEMA_VERSION, _now()))
        conn.commit()


def current_version(conn):
    row = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    return row[0] if row else None


def _sqlite(path):
    import catalog   # applies core+ops+admin+catalog schema with FKs + Row factory
    conn = catalog.connect_full(path)
    ensure_version(conn)
    return conn


def _postgres(url):
    try:
        import psycopg2
    except Exception:
        raise RuntimeError(
            "PostgreSQL selected via DATABASE_URL but psycopg2 is not installed. "
            "Add `psycopg2-binary` (see requirements.txt) and rebuild. The RGO image "
            "installs it. (fixable without owner cloud credentials)")
    import dbconn
    try:
        raw = psycopg2.connect(url)                 # real connection to a real server
    except Exception as e:
        raise RuntimeError(
            f"could not connect to PostgreSQL ({e}). Run `docker compose up` (bundles "
            f"postgres:16) or point DATABASE_URL at a reachable server, then `python migrate.py`. "
            f"(needs a running PostgreSQL — not owner cloud credentials)")
    raw.autocommit = False
    conn = dbconn.PgConnection(raw)
    dbconn.apply_schema(conn)                       # idempotent DDL (dialect-translated)
    ensure_version(conn)
    return conn


def _seed_platform(conn):
    """Ensure Platform-1 tables + seed exist (tenants, roles, config, org hierarchy).
    Idempotent; additive to the operational schema. Keeps the running server/HTTP path
    governable."""
    import admin_platform
    import org
    import backfill
    import config_registry
    import masterdata
    import crm_admin
    import workflow
    import wfgov
    import forms
    import settings as sysconfig
    import integrations
    import reporting
    import ai_admin
    import saas
    import marketplace
    import marketplace_onboarding
    import marketplace_matching
    import marketplace_payments
    import marketplace_trips
    admin_platform.init(conn)
    config_registry.init(conn); config_registry.seed(conn)   # definitions before values (Phase 2)
    admin_platform.seed(conn)
    org.init(conn)
    backfill.init(conn)
    backfill.add_tenant_columns(conn)   # operational tables carry tenant_id from the start
    masterdata.init(conn); masterdata.seed(conn)             # Phase 3: canonical master data (platform scope)
    crm_admin.init(conn); crm_admin.seed(conn)               # Phase 3: CRM administration (numbering/credit/dup/custom)
    wfgov.init(conn)                                          # Phase 4: approval/SLA/escalation/delegation tables
    workflow.init(conn); workflow.seed(conn)                 # Phase 4: governed workflow engine + imported booking def
    forms.init(conn); forms.seed(conn)                        # Phase 5: governed form & custom-field engine + booking form
    sysconfig.init(conn); sysconfig.seed(conn)                # Phase 6: platform & system settings + modules + security floors
    integrations.init(conn); integrations.seed(conn)          # Phase 7: integration definitions/profiles/webhooks/reconciliation + Wise
    reporting.init(conn); reporting.seed(conn)                # Phase 8: governed reporting datasets + standard reports + dashboards
    ai_admin.init(conn); ai_admin.seed(conn)                  # Phase 9: governed AI use cases/models/prompts/tools + mock provider
    saas.init(conn); saas.seed(conn)                          # Phase 10: SaaS product catalog + subscriptions + entitlements + metering
    marketplace.init(conn); marketplace.seed(conn)            # Marketplace foundation: cargo/vehicle taxonomy + eligibility + lanes
    marketplace_onboarding.init(conn); marketplace_onboarding.seed(conn)  # Marketplace Inc.2: shipper/carrier/vehicle/driver onboarding + compliance
    marketplace_matching.init(conn); marketplace_matching.seed(conn)     # Marketplace Inc.3: booking/pricing/matching/offers/assignment
    marketplace_payments.init(conn); marketplace_payments.seed(conn)     # Marketplace Inc.4: protected payment/release/payout/disputes/refunds
    marketplace_trips.init(conn); marketplace_trips.seed(conn)           # Marketplace Inc.5: trip execution/GPS/geofence/proof-of-delivery
    import marketplace_trust
    marketplace_trust.init(conn); marketplace_trust.seed(conn)           # Trust: KYB verification/fraud/trust-score/eligibility gate
    import core, rates
    core._migrate_pricing(conn)                              # quotation pricing: add line columns on existing DBs
    rates.seed_default_rate_cards(conn)                      # governed baseline rate catalog (idempotent)
    return conn


def connect(url: str | None = None):
    url = url if url is not None else os.environ.get("DATABASE_URL")
    if url and url.startswith(("postgres://", "postgresql://")):
        return _seed_platform(_postgres(url))
    if not url or url in (":memory:", "sqlite://:memory:", "sqlite::memory:"):
        return _seed_platform(_sqlite(":memory:"))
    if url.startswith("sqlite:///"):
        url = url[len("sqlite:///"):]
    elif url.startswith("sqlite://"):
        url = url[len("sqlite://"):]
    return _seed_platform(_sqlite(url or "rgo_os.sqlite"))
