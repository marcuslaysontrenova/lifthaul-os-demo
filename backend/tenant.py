"""LiftHaul OS — server-side tenant isolation (Phase 1 Items 1-2).

The authoritative tenant-context + enforcement layer for operational access. Tenant
context is derived ONLY from the authenticated identity (`users.tenant_id`, surfaced on
the actor) — never from client-supplied tenant_id in query/route/body/headers.

Enforcement rule (backward-compatible by design):
    deny only when BOTH the actor tenant and the record tenant are set AND differ.
A null tenant on either side is treated as the single-tenant/legacy case and allowed,
so pre-backfill data and existing tests keep working; isolation activates the moment
records and users carry tenants (the two-tenant validation).

Unauthorized reads raise NotFoundError (404) — never reveal that another tenant's record
exists. Cross-tenant relationships and writes raise ForbiddenError (403).
"""
from __future__ import annotations

import core

CROSS_ACCESS_PERMISSION = "platform.tenant.cross_access"


def actor_tenant(actor):
    return (actor or {}).get("tenant_id")


def _row_tenant(record):
    if record is None:
        return None
    try:
        keys = record.keys()
    except AttributeError:
        return None
    return record["tenant_id"] if "tenant_id" in keys else None


def can_cross(actor) -> bool:
    """Platform actors holding the explicit cross-access permission bypass tenant scope."""
    perms = (actor or {}).get("perms")
    if perms is None:
        perms = core.PERMISSIONS.get((actor or {}).get("role"), set())
    return CROSS_ACCESS_PERMISSION in perms or "*" in perms


def guard(actor, record):
    """Read guard: 404 (no existence leak) when the record belongs to another tenant."""
    at, rt = actor_tenant(actor), _row_tenant(record)
    if at is not None and rt is not None and at != rt and not can_cross(actor):
        raise core.NotFoundError("record not found")
    return record


def stamp(conn, actor, table, record_id):
    """Assign the actor's tenant to a freshly-created record (server-derived ownership).
    No-op when the actor has no tenant (single-tenant/legacy) or the column is absent."""
    at = actor_tenant(actor)
    if at is None:
        return
    try:
        conn.execute(f"UPDATE {table} SET tenant_id=? WHERE id=? AND tenant_id IS NULL", (at, record_id))
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception: pass


def assert_related(conn, actor, table, record_id):
    """Relationship isolation: a referenced record must be in the actor's tenant."""
    at = actor_tenant(actor)
    if at is None or record_id is None:
        return
    try:
        row = conn.execute(f"SELECT tenant_id FROM {table} WHERE id=?", (record_id,)).fetchone()
    except Exception:
        try: conn.rollback()
        except Exception: pass
        return
    rt = row["tenant_id"] if row is not None and "tenant_id" in row.keys() else None
    if rt is not None and rt != at and not can_cross(actor):
        raise core.ForbiddenError("cross-tenant relationship is not allowed")


def predicate(actor):
    """(sql_fragment, params) to scope a list/search query to the actor's tenant.
    Legacy NULL-tenant rows remain visible in single-tenant mode."""
    at = actor_tenant(actor)
    if at is None or can_cross(actor):
        return "", ()
    return " AND (tenant_id = ? OR tenant_id IS NULL)", (at,)


def bind_user_tenant(conn, actor, user_id, tenant_id):
    """Set a user's home tenant (the authoritative membership). Audited."""
    conn.execute("UPDATE users SET tenant_id=? WHERE id=?", (tenant_id, user_id))
    if actor:
        core.audit(conn, actor, "USER_TENANT_BOUND", "users", user_id, new={"tenant_id": tenant_id})
    conn.commit()
