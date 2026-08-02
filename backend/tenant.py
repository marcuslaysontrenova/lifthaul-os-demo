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

import datetime

import core

CROSS_ACCESS_PERMISSION = "platform.tenant.cross_access"
CROSS_ACCESS_DEFAULT_TTL = 900        # 15 minutes
CROSS_ACCESS_MAX_TTL = 3600           # 1 hour hard cap — no permanent platform browsing


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


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
    """Cross-tenant access requires an ACTIVE, UNEXPIRED grant (set on the actor by
    core.actor_for) — NOT merely holding the permission. This forbids a permanent
    platform-wide browsing session: the permission only lets you *activate* a grant."""
    return bool((actor or {}).get("cross_access"))


def has_cross_permission(actor) -> bool:
    perms = (actor or {}).get("perms")
    if perms is None:
        perms = core.PERMISSIONS.get((actor or {}).get("role"), set())
    return CROSS_ACCESS_PERMISSION in perms or "*" in perms


# ---- expiring platform cross-access (Item 5) ------------------------------- #
def activate_cross_access(conn, actor, target_tenant, reason, ttl=CROSS_ACCESS_DEFAULT_TTL):
    if not has_cross_permission(actor):
        raise core.ForbiddenError("platform.tenant.cross_access permission required")
    if not target_tenant or not reason:
        raise core.ValidationError("target_tenant and reason are required")
    ttl = min(int(ttl or CROSS_ACCESS_DEFAULT_TTL), CROSS_ACCESS_MAX_TTL)
    now = _now()
    cid = core.correlation_id()
    cur = conn.execute(
        "INSERT INTO cross_access_grants(user_id,source_tenant,target_tenant,reason,correlation_id,"
        "activated_at,expires_at,status) VALUES(?,?,?,?,?,?,?, 'ACTIVE')",
        (actor["id"], actor.get("tenant_id"), target_tenant, reason, cid, _iso(now),
         _iso(now + datetime.timedelta(seconds=ttl))))
    gid = cur.lastrowid
    core.audit(conn, actor, "PLATFORM_CROSS_ACCESS_ACTIVATED", "cross_access_grants", gid,
               new={"target_tenant": target_tenant, "reason": reason, "ttl_seconds": ttl,
                    "severity": "HIGH"})
    conn.commit()
    return {"grant_id": gid, "target_tenant": target_tenant, "expires_at": _iso(now + datetime.timedelta(seconds=ttl)),
            "correlation_id": cid, "ttl_seconds": ttl}


def active_cross_grant(conn, user_id):
    """The user's current active, unexpired, non-terminated grant (or None). Tolerant of
    a missing table (pre-migration / non-seeded connections)."""
    try:
        now = _iso(_now())
        return conn.execute(
            "SELECT * FROM cross_access_grants WHERE user_id=? AND status='ACTIVE'"
            " AND terminated_at IS NULL AND expires_at > ? ORDER BY id DESC LIMIT 1",
            (user_id, now)).fetchone()
    except Exception:
        try: conn.rollback()
        except Exception: pass
        return None


def terminate_cross_access(conn, actor, grant_id):
    conn.execute("UPDATE cross_access_grants SET status='TERMINATED', terminated_at=? WHERE id=?",
                 (_iso(_now()), grant_id))
    core.audit(conn, actor, "PLATFORM_CROSS_ACCESS_TERMINATED", "cross_access_grants", grant_id,
               new={"severity": "HIGH"})
    conn.commit()


def enrich_cross_access(conn, actor):
    """Set actor['cross_access'] True iff the user holds an active, unexpired grant.
    Called by core.actor_for so guards can decide with the actor alone."""
    g = active_cross_grant(conn, actor["id"])
    if g:
        actor["cross_access"] = True
        actor["cross_access_target"] = g["target_tenant"]
    return actor


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
