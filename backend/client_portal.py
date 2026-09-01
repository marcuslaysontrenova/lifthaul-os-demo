"""Registered client workspace over the canonical LiftHaul marketplace.

The portal is deliberately an access and projection layer.  It does not create a second booking,
offer, trip, payment or dispute domain.  A login is bound to one marketplace shipper and every read
is derived from that identity; callers cannot select another shipper with a request parameter.

Payment preferences store provider-issued aliases only.  Raw card, bank or wallet credentials are
never accepted or persisted by this module.
"""
from __future__ import annotations

import datetime
import re

import core
import tenant
import protected_payment as pp


_RAW_ACCOUNT_NUMBER = re.compile(r"(?<!\d)\d{12,19}(?!\d)")


PORTAL_PERMISSIONS = (
    "client.portal.view",
    "client.portal.booking.manage",
    "client.portal.address.manage",
    "client.portal.payment_preference.manage",
    "client.portal.dispute.manage",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS client_principals(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  user_id INTEGER NOT NULL,
  shipper_id INTEGER NOT NULL,
  portal_role TEXT NOT NULL DEFAULT 'CLIENT_BOOKER',
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT,
  revoked_by INTEGER, revoked_at TEXT,
  UNIQUE(user_id, shipper_id));

CREATE TABLE IF NOT EXISTS client_saved_addresses(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  shipper_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  island_group TEXT, region_code TEXT, province_code TEXT,
  city_code TEXT, barangay_code TEXT, specific_address TEXT NOT NULL,
  latitude REAL, longitude REAL,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS client_payment_preferences(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  shipper_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  channel TEXT NOT NULL,
  provider_alias TEXT NOT NULL,
  display_label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING_PROVIDER_VERIFICATION',
  is_default INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS client_notification_reads(
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  user_id INTEGER NOT NULL,
  notification_id INTEGER NOT NULL,
  read_at TEXT NOT NULL,
  UNIQUE(user_id, notification_id));
"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return 0


def bind_principal(conn, actor, user_id, shipper_id, portal_role="CLIENT_BOOKER"):
    core.require(actor, "marketplace.shipper.application.manage")
    shipper = conn.execute("SELECT * FROM mkt_shippers WHERE id=?", (shipper_id,)).fetchone()
    if not shipper:
        raise core.NotFoundError("shipper not found")
    tenant.guard(actor, shipper)
    existing = conn.execute(
        "SELECT id FROM client_principals WHERE user_id=? AND shipper_id=?", (user_id, shipper_id)
    ).fetchone()
    if existing:
        pid = existing["id"]
        conn.execute(
            "UPDATE client_principals SET portal_role=?,status='ACTIVE',revoked_by=NULL,revoked_at=NULL WHERE id=?",
            (portal_role, pid),
        )
    else:
        cur = conn.execute(
            "INSERT INTO client_principals(user_id,shipper_id,portal_role,status,created_by,created_at) "
            "VALUES(?,?,?,'ACTIVE',?,?)",
            (user_id, shipper_id, portal_role, actor["id"], _now()),
        )
        pid = cur.lastrowid
        tenant.stamp(conn, actor, "client_principals", pid)
    core.audit(conn, actor, "CLIENT_PRINCIPAL_BOUND", "client_principals", pid,
               new={"user_id": user_id, "shipper_id": shipper_id, "portal_role": portal_role})
    conn.commit()
    return pid


def revoke_principal(conn, actor, principal_id, reason=None):
    core.require(actor, "marketplace.shipper.application.manage")
    row = conn.execute("SELECT * FROM client_principals WHERE id=?", (principal_id,)).fetchone()
    if not row:
        raise core.NotFoundError("client principal not found")
    tenant.guard(actor, row)
    conn.execute(
        "UPDATE client_principals SET status='REVOKED',revoked_by=?,revoked_at=? WHERE id=?",
        (actor["id"], _now(), principal_id),
    )
    core.audit(conn, actor, "CLIENT_PRINCIPAL_REVOKED", "client_principals", principal_id,
               new={"reason": reason})
    conn.commit()
    return {"status": "REVOKED"}


def _binding(conn, actor):
    return conn.execute(
        "SELECT * FROM client_principals WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1",
        (actor["id"],),
    ).fetchone()


def resolve_shipper(conn, actor):
    binding = _binding(conn, actor)
    if not binding:
        raise core.ForbiddenError("no active client workspace binding for this user")
    return binding["shipper_id"]


def _shipper(conn, actor):
    sid = resolve_shipper(conn, actor)
    row = conn.execute("SELECT * FROM mkt_shippers WHERE id=?", (sid,)).fetchone()
    if not row:
        raise core.NotFoundError("shipper not found")
    tenant.guard(actor, row)
    return sid, dict(row)


def _bookings(conn, actor):
    sid = resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT * FROM mkt_bookings WHERE shipper_id=? ORDER BY id DESC", (sid,)
    ).fetchall()
    return sid, [dict(r) for r in rows]


_DRAFT = {"DRAFT", "RETURNED", "REVISION_REQUIRED"}
_ACTIVE = {"SUBMITTED", "VALIDATED", "BROADCAST", "OFFER_AVAILABLE", "SELECTED", "ASSIGNED",
           "PAYMENT_REQUIRED", "PAYMENT_PENDING", "CONFIRMED", "READY_FOR_TRIP_ACTIVATION",
           "IN_TRANSIT", "SERVICE_IN_PROGRESS", "DELIVERY_PENDING", "DELIVERY_SUBMITTED"}
_COMPLETED = {"COMPLETED", "DELIVERED", "SETTLED", "CLOSED"}
_CANCELLED = {"CANCELLED", "EXPIRED", "REJECTED"}


def _booking_bucket(status):
    value = str(status or "UNKNOWN").upper()
    if value in _DRAFT:
        return "DRAFT"
    if value in _COMPLETED:
        return "COMPLETED"
    if value in _CANCELLED:
        return "CANCELLED"
    if value in _ACTIVE:
        return "ACTIVE"
    return "OTHER"


def _booking_action(status, payment_state=None, trip_status=None):
    bucket = _booking_bucket(status)
    if bucket == "DRAFT":
        return "Continue Draft"
    if str(payment_state or "").upper() in ("PAYMENT_REQUIRED", "PAYMENT_PENDING"):
        return "Pay Now"
    if str(trip_status or "").upper() in ("IN_TRANSIT", "SERVICE_IN_PROGRESS", "PICKED_UP"):
        return "Track Booking"
    if str(payment_state or "").upper() in ("DISPUTE_WINDOW", "DELIVERY_EVIDENCE_PENDING"):
        return "Confirm Delivery"
    if bucket == "COMPLETED":
        return "Download Receipt"
    return "View Details"


def _booking_view(conn, row):
    b = dict(row)
    assignment = conn.execute(
        "SELECT * FROM mkt_assignments WHERE booking_id=? AND status<>'CANCELLED' ORDER BY id DESC LIMIT 1",
        (b["id"],),
    ).fetchone()
    carrier = vehicle = trip = pricing = payment = None
    if assignment:
        carrier = conn.execute("SELECT legal_name,trade_name FROM mkt_carriers WHERE id=?",
                               (assignment["carrier_id"],)).fetchone()
        vehicle = conn.execute("SELECT category_code,plate_number,body_type FROM mkt_vehicles WHERE id=?",
                               (assignment["vehicle_id"],)).fetchone()
        trip = conn.execute("SELECT id,status,progress_pct,eta,last_ping_at FROM mkt_trips "
                            "WHERE assignment_id=? ORDER BY id DESC LIMIT 1", (assignment["id"],)).fetchone()
        if assignment["pricing_snapshot_id"]:
            pricing = conn.execute("SELECT total,currency FROM mkt_pricing_snapshots WHERE id=?",
                                   (assignment["pricing_snapshot_id"],)).fetchone()
    payment = conn.execute("SELECT id,state,contract_amount,updated_at FROM mkt_protected_tx "
                           "WHERE booking_id=? ORDER BY id DESC LIMIT 1", (b["id"],)).fetchone()
    payment_state = payment["state"] if payment else b.get("payment_status")
    return {
        "id": b["id"], "booking_reference": f"LH-{int(b['id']):06d}",
        "created_at": b.get("created_at"), "updated_at": b.get("updated_at") or b.get("created_at"),
        "pickup_location": b.get("pickup_address") or b.get("origin_zone") or "Not set",
        "delivery_location": b.get("delivery_address") or b.get("dest_zone") or "Not set",
        "pickup_schedule": b.get("pickup_window"),
        "cargo_type": b.get("cargo_description") or b.get("cargo_code"),
        "cargo_description": b.get("cargo_description"),
        "origin_zone": b.get("origin_zone"), "dest_zone": b.get("dest_zone"),
        "requested_vehicle": b.get("requested_vehicle_category"),
        "assigned_truck": (" · ".join(x for x in (
            vehicle["category_code"] if vehicle else None,
            vehicle["plate_number"] if vehicle else None) if x) or None),
        "service_provider": ((carrier["trade_name"] or carrier["legal_name"]) if carrier else None),
        "booking_amount": (pricing["total"] if pricing else (payment["contract_amount"] if payment else None)),
        "currency": (pricing["currency"] if pricing else "PHP"),
        "status": b.get("status"), "status_bucket": _booking_bucket(b.get("status")),
        "protected_payment_status": payment_state,
        "protected_payment_id": payment["id"] if payment else None,
        "trip_id": trip["id"] if trip else None,
        "trip_status": trip["status"] if trip else None,
        "progress_pct": trip["progress_pct"] if trip else None,
        "eta": trip["eta"] if trip else None,
        "available_action": _booking_action(b.get("status"), payment_state, trip["status"] if trip else None),
    }


def overview(conn, actor):
    core.require(actor, "client.portal.view")
    sid, shipper = _shipper(conn, actor)
    _, bookings = _bookings(conn, actor)
    projected = [_booking_view(conn, booking) for booking in bookings]
    by_status = {}
    buckets = {"DRAFT": 0, "ACTIVE": 0, "COMPLETED": 0, "CANCELLED": 0}
    for booking in projected:
        raw = booking.get("status") or "UNKNOWN"
        by_status[raw] = by_status.get(raw, 0) + 1
        if booking["status_bucket"] in buckets:
            buckets[booking["status_bucket"]] += 1
    unread = conn.execute(
        "SELECT COUNT(*) n FROM notifications n WHERE n.recipient=? AND NOT EXISTS("
        "SELECT 1 FROM client_notification_reads r WHERE r.user_id=? AND r.notification_id=n.id)",
        (actor.get("email") or "", actor["id"]),
    ).fetchone()
    payment_rows = conn.execute(
        "SELECT p.id,p.state FROM mkt_protected_tx p JOIN mkt_bookings b ON b.id=p.booking_id "
        "WHERE b.shipper_id=? ORDER BY p.id DESC", (sid,)).fetchall()
    open_disputes = conn.execute(
        "SELECT COUNT(*) n FROM mkt_disputes d JOIN mkt_bookings b ON b.id=d.booking_id "
        "WHERE b.shipper_id=? AND d.status NOT IN('RESOLVED','CLOSED','REJECTED')", (sid,)).fetchone()
    payments_recent = []
    for row in payment_rows[:3]:
        payments_recent.append(_payment_view(conn, actor, row["id"]))
    action_required = [b for b in projected if b["available_action"] in (
        "Continue Draft", "Pay Now", "Confirm Delivery")][:5]
    return {
        "shipper_id": sid,
        "legal_name": shipper.get("legal_name"),
        "verification_status": shipper.get("status"),
        "booking_counts": by_status,
        "summary": {"total": len(projected), "draft": buckets["DRAFT"], "active": buckets["ACTIVE"],
                    "completed": buckets["COMPLETED"], "cancelled": buckets["CANCELLED"],
                    "protected_payments": len(payment_rows),
                    "open_disputes": open_disputes["n"] if open_disputes else 0,
                    "unread_notifications": unread["n"] if unread else 0},
        "total_bookings": len(bookings),
        "unread_notifications": unread["n"] if unread else 0,
        "action_required": action_required,
        "recent_bookings": projected[:5],
        "recent_payments": payments_recent,
    }


def bookings(conn, actor):
    core.require(actor, "client.portal.view")
    sid, rows = _bookings(conn, actor)
    return {"shipper_id": sid, "bookings": [_booking_view(conn, row) for row in rows]}


def offers(conn, actor):
    core.require(actor, "client.portal.view")
    sid = resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT o.* FROM mkt_offers o JOIN mkt_bookings b ON b.id=o.booking_id "
        "WHERE b.shipper_id=? ORDER BY o.id DESC", (sid,)
    ).fetchall()
    # Client comparison never exposes internal platform margin or unrelated carrier data.
    allowed = ("id", "booking_id", "carrier_id", "amount", "currency", "status", "submitted_at",
               "valid_until", "vehicle_id", "driver_id", "transit_hours", "notes")
    return {"shipper_id": sid, "offers": [{k: dict(r).get(k) for k in allowed if k in r.keys()} for r in rows]}


def trips(conn, actor):
    core.require(actor, "client.portal.view")
    sid = resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT t.* FROM mkt_trips t JOIN mkt_assignments a ON a.id=t.assignment_id "
        "JOIN mkt_bookings b ON b.id=a.booking_id WHERE b.shipper_id=? ORDER BY t.id DESC", (sid,)
    ).fetchall()
    return {"shipper_id": sid, "trips": [dict(r) for r in rows]}


def _payment_view(conn, actor, tx_id):
    view = pp.customer_view(conn, actor, tx_id)
    tx = conn.execute("SELECT created_at,updated_at,booking_id,carrier_id FROM mkt_protected_tx WHERE id=?",
                      (tx_id,)).fetchone()
    booking = conn.execute("SELECT pickup_address,origin_zone,delivery_address,dest_zone FROM mkt_bookings WHERE id=?",
                           (tx["booking_id"],)).fetchone()
    carrier = conn.execute("SELECT legal_name,trade_name FROM mkt_carriers WHERE id=?", (tx["carrier_id"],)).fetchone()
    view.update({
        "created_at": tx["created_at"], "last_status_update": tx["updated_at"] or tx["created_at"],
        "pickup_location": booking["pickup_address"] or booking["origin_zone"],
        "delivery_location": booking["delivery_address"] or booking["dest_zone"],
        "service_provider": ((carrier["trade_name"] or carrier["legal_name"]) if carrier else view["service_provider"]),
        "payment_method": view.get("provider") or "Provider checkout",
        "amount_protected": view.get("protected_amount"),
        "release_date": view.get("dispute_window_expires_at"),
        "dispute_status": ("OPEN" if view.get("state") in ("DISPUTED", "LEGAL_HOLD") else "NONE"),
    })
    return view


def payments(conn, actor):
    core.require(actor, "client.portal.view")
    sid = resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT p.id FROM mkt_protected_tx p JOIN mkt_bookings b ON b.id=p.booking_id "
        "WHERE b.shipper_id=? ORDER BY p.id DESC", (sid,)
    ).fetchall()
    return {"shipper_id": sid, "payments": [_payment_view(conn, actor, r["id"]) for r in rows]}


def payment_detail(conn, actor, tx_id):
    core.require(actor, "client.portal.view")
    sid = resolve_shipper(conn, actor)
    owned = conn.execute(
        "SELECT 1 FROM mkt_protected_tx p JOIN mkt_bookings b ON b.id=p.booking_id "
        "WHERE p.id=? AND b.shipper_id=?", (tx_id, sid)).fetchone()
    if not owned:
        raise core.NotFoundError("protected-payment transaction not found")
    return _payment_view(conn, actor, tx_id)


def addresses(conn, actor):
    core.require(actor, "client.portal.view")
    sid = resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT * FROM client_saved_addresses WHERE shipper_id=? AND status='ACTIVE' ORDER BY id DESC", (sid,)
    ).fetchall()
    return {"shipper_id": sid, "addresses": [dict(r) for r in rows]}


def add_address(conn, actor, label, specific_address, **attrs):
    core.require(actor, "client.portal.address.manage")
    sid = resolve_shipper(conn, actor)
    if not str(label or "").strip() or not str(specific_address or "").strip():
        raise core.ValidationError("label and specific address are required")
    cur = conn.execute(
        "INSERT INTO client_saved_addresses(shipper_id,label,island_group,region_code,province_code,city_code,"
        "barangay_code,specific_address,latitude,longitude,status,created_by,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?)",
        (sid, label.strip(), attrs.get("island_group"), attrs.get("region_code"), attrs.get("province_code"),
         attrs.get("city_code"), attrs.get("barangay_code"), specific_address.strip(), attrs.get("latitude"),
         attrs.get("longitude"), actor["id"], _now(), _now()),
    )
    aid = cur.lastrowid
    tenant.stamp(conn, actor, "client_saved_addresses", aid)
    core.audit(conn, actor, "CLIENT_ADDRESS_CREATED", "client_saved_addresses", aid,
               new={"shipper_id": sid, "label": label})
    conn.commit()
    return {"id": aid, "status": "ACTIVE"}


def payment_preferences(conn, actor):
    core.require(actor, "client.portal.view")
    sid = resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT id,provider,channel,display_label,status,is_default,created_at "
        "FROM client_payment_preferences WHERE shipper_id=? ORDER BY is_default DESC,id DESC", (sid,)
    ).fetchall()
    return {"shipper_id": sid, "payment_preferences": [dict(r) for r in rows]}


def add_payment_preference(conn, actor, provider, channel, provider_alias, display_label, is_default=False):
    core.require(actor, "client.portal.payment_preference.manage")
    sid = resolve_shipper(conn, actor)
    if not all(str(v or "").strip() for v in (provider, channel, provider_alias, display_label)):
        raise core.ValidationError("provider, channel, provider alias and display label are required")
    if _RAW_ACCOUNT_NUMBER.search(str(provider_alias).replace(" ", "").replace("-", "")):
        raise core.ValidationError("raw card or bank account numbers are not accepted; use a provider token")
    if _RAW_ACCOUNT_NUMBER.search(str(display_label).replace(" ", "").replace("-", "")):
        raise core.ValidationError("display labels must be masked")
    if is_default:
        conn.execute("UPDATE client_payment_preferences SET is_default=0 WHERE shipper_id=?", (sid,))
    cur = conn.execute(
        "INSERT INTO client_payment_preferences(shipper_id,provider,channel,provider_alias,display_label,status,"
        "is_default,created_by,created_at,updated_at) VALUES(?,?,?,?,?,'PENDING_PROVIDER_VERIFICATION',?,?,?,?)",
        (sid, provider, channel, provider_alias, display_label, 1 if is_default else 0,
         actor["id"], _now(), _now()),
    )
    pid = cur.lastrowid
    tenant.stamp(conn, actor, "client_payment_preferences", pid)
    core.audit(conn, actor, "CLIENT_PAYMENT_PREFERENCE_ADDED", "client_payment_preferences", pid,
               new={"shipper_id": sid, "provider": provider, "channel": channel})
    conn.commit()
    return {"id": pid, "status": "PENDING_PROVIDER_VERIFICATION"}


def notifications(conn, actor):
    core.require(actor, "client.portal.view")
    resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT n.id,n.event_type,n.subject,n.body,n.channel,n.status,n.created_at,"
        "CASE WHEN r.id IS NULL THEN 0 ELSE 1 END AS is_read "
        "FROM notifications n LEFT JOIN client_notification_reads r "
        "ON r.notification_id=n.id AND r.user_id=? WHERE n.recipient=? ORDER BY n.id DESC",
        (actor["id"], actor.get("email") or ""),
    ).fetchall()
    return {"notifications": [dict(r) for r in rows]}


def mark_notification_read(conn, actor, notification_id):
    core.require(actor, "client.portal.view")
    resolve_shipper(conn, actor)
    row = conn.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
    if not row or row["recipient"] != (actor.get("email") or ""):
        raise core.NotFoundError("notification not found")
    existing = conn.execute(
        "SELECT id FROM client_notification_reads WHERE user_id=? AND notification_id=?",
        (actor["id"], notification_id),
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO client_notification_reads(user_id,notification_id,read_at) VALUES(?,?,?)",
            (actor["id"], notification_id, _now()),
        )
    conn.commit()
    return {"id": notification_id, "read": True}


def mark_all_notifications_read(conn, actor):
    core.require(actor, "client.portal.view")
    resolve_shipper(conn, actor)
    rows = conn.execute(
        "SELECT id FROM notifications WHERE recipient=?", (actor.get("email") or "",)).fetchall()
    changed = 0
    for row in rows:
        if not conn.execute("SELECT 1 FROM client_notification_reads WHERE user_id=? AND notification_id=?",
                            (actor["id"], row["id"])).fetchone():
            conn.execute("INSERT INTO client_notification_reads(user_id,notification_id,read_at) VALUES(?,?,?)",
                         (actor["id"], row["id"], _now()))
            changed += 1
    conn.commit()
    return {"read": True, "updated": changed}
