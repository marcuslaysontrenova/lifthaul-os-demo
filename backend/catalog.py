"""RGO OS backend — remaining master/support entities (§23) with controls.

Adds: contacts, addresses, equipment, vehicles, employees, maintenance work
orders (+ equipment-availability effect), inspections, supplier invoices,
invoice lines, booking messages (internal vs customer-visible), system config.
"""
from __future__ import annotations

import core
import ops   # noqa: F401  — ensures base ops/admin roles (fleet_manager, mechanic, ...) are registered
import admin  # noqa: F401
from core import require, audit, now, NotFoundError, ValidationError

core.PERMISSIONS["operations_manager"] |= {"contact.*", "address.*", "equipment.*", "vehicle.*",
                                           "employee.*", "maintenance.*", "inspection.*",
                                           "message.*", "config.read"}
core.PERMISSIONS["fleet_manager"] |= {"equipment.*", "vehicle.*", "maintenance.*", "inspection.*"}
core.PERMISSIONS["mechanic"] |= {"maintenance.create", "maintenance.close", "inspection.create"}
core.PERMISSIONS["finance"] |= {"supplierinvoice.*"}

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  name TEXT, email TEXT, phone TEXT, role TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS addresses(
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  kind TEXT, line TEXT, city TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS equipment(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT, etype TEXT, capacity TEXT,
  status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS vehicles(
  id INTEGER PRIMARY KEY, plate TEXT UNIQUE NOT NULL, vtype TEXT, status TEXT DEFAULT 'ACTIVE',
  created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS employees(
  id INTEGER PRIMARY KEY, name TEXT, role TEXT, user_id INTEGER REFERENCES users(id),
  status TEXT DEFAULT 'ACTIVE', created_at TEXT);

CREATE TABLE IF NOT EXISTS maintenance_work_orders(
  id INTEGER PRIMARY KEY, no TEXT UNIQUE NOT NULL, equipment_code TEXT, mtype TEXT,
  status TEXT DEFAULT 'OPEN', cost REAL, opened_by INTEGER, opened_at TEXT, closed_at TEXT);

CREATE TABLE IF NOT EXISTS inspections(
  id INTEGER PRIMARY KEY, equipment_code TEXT, itype TEXT, result TEXT, inspected_at TEXT, inspector INTEGER);

CREATE TABLE IF NOT EXISTS supplier_invoices(
  id INTEGER PRIMARY KEY, no TEXT, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  po_id INTEGER, amount REAL, status TEXT DEFAULT 'RECEIVED', created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS booking_messages(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  sender TEXT, visibility TEXT NOT NULL, body TEXT, ts TEXT, read INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS system_config(
  key TEXT PRIMARY KEY, value TEXT, updated_by INTEGER, updated_at TEXT);
"""


def init_catalog(conn):
    conn.executescript(CATALOG_SCHEMA)
    conn.commit()


def connect_full(path=":memory:"):
    import admin
    conn = admin.connect_full(path)
    init_catalog(conn)
    return conn


# contacts / addresses -------------------------------------------------------
def add_contact(conn, actor, customer_id, name, email=None, phone=None, role=None):
    require(actor, "contact.create")
    cur = conn.execute("INSERT INTO contacts(customer_id,name,email,phone,role,created_at) VALUES(?,?,?,?,?,?)",
                       (customer_id, name, email, phone, role, now()))
    conn.commit(); audit(conn, actor, "contact.create", "contact", cur.lastrowid, new={"name": name}); conn.commit()
    return cur.lastrowid


def add_address(conn, actor, customer_id, kind, line, city=None):
    require(actor, "address.create")
    cur = conn.execute("INSERT INTO addresses(customer_id,kind,line,city,created_at) VALUES(?,?,?,?,?)",
                       (customer_id, kind, line, city, now()))
    conn.commit(); return cur.lastrowid


# equipment / vehicles / employees ------------------------------------------
def add_equipment(conn, actor, code, name, etype, capacity=None):
    require(actor, "equipment.create")
    try:
        cur = conn.execute("INSERT INTO equipment(code,name,etype,capacity,created_by,created_at)"
                           " VALUES(?,?,?,?,?,?)", (code, name, etype, capacity, actor["id"], now()))
    except Exception:
        from core import ConflictError
        raise ConflictError("duplicate equipment code")
    conn.commit(); audit(conn, actor, "equipment.create", "equipment", cur.lastrowid, new={"code": code}); conn.commit()
    return cur.lastrowid


def equipment_available(conn, code):
    r = conn.execute("SELECT status FROM equipment WHERE code=?", (code,)).fetchone()
    return (r is None) or r["status"] == "ACTIVE"   # unknown code => not managed here => available


def add_vehicle(conn, actor, plate, vtype):
    require(actor, "vehicle.create")
    cur = conn.execute("INSERT INTO vehicles(plate,vtype,created_by,created_at) VALUES(?,?,?,?)",
                       (plate, vtype, actor["id"], now()))
    conn.commit(); return cur.lastrowid


def add_employee(conn, actor, name, role, user_id=None):
    require(actor, "employee.create")
    cur = conn.execute("INSERT INTO employees(name,role,user_id,created_at) VALUES(?,?,?,?)",
                       (name, role, user_id, now()))
    conn.commit(); return cur.lastrowid


# maintenance work orders (make equipment unavailable) ----------------------
def open_work_order(conn, actor, equipment_code, mtype):
    require(actor, "maintenance.create")
    n = conn.execute("SELECT COUNT(*) c FROM maintenance_work_orders").fetchone()["c"]
    no = f"WO-{4001 + n}"
    with conn:
        cur = conn.execute("INSERT INTO maintenance_work_orders(no,equipment_code,mtype,opened_by,opened_at)"
                           " VALUES(?,?,?,?,?)", (no, equipment_code, mtype, actor["id"], now()))
        conn.execute("UPDATE equipment SET status='MAINTENANCE' WHERE code=?", (equipment_code,))
        audit(conn, actor, "maintenance.open", "work_order", cur.lastrowid,
              new={"equipment": equipment_code, "effect": "equipment -> MAINTENANCE"})
    return cur.lastrowid


def close_work_order(conn, actor, wo_id, cost=0):
    require(actor, "maintenance.close")
    wo = conn.execute("SELECT * FROM maintenance_work_orders WHERE id=?", (wo_id,)).fetchone()
    if not wo:
        raise NotFoundError("work order not found")
    with conn:
        conn.execute("UPDATE maintenance_work_orders SET status='DONE', cost=?, closed_at=? WHERE id=?",
                     (cost, now(), wo_id))
        conn.execute("UPDATE equipment SET status='ACTIVE' WHERE code=?", (wo["equipment_code"],))
        audit(conn, actor, "maintenance.close", "work_order", wo_id, new={"status": "DONE"})


def add_inspection(conn, actor, equipment_code, itype, result):
    require(actor, "inspection.create")
    cur = conn.execute("INSERT INTO inspections(equipment_code,itype,result,inspected_at,inspector)"
                       " VALUES(?,?,?,?,?)", (equipment_code, itype, result, now(), actor["id"]))
    conn.commit(); return cur.lastrowid


# supplier invoices ----------------------------------------------------------
def add_supplier_invoice(conn, actor, supplier_id, amount, po_id=None, no=None):
    require(actor, "supplierinvoice.create")
    if not conn.execute("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)).fetchone():
        raise NotFoundError("supplier not found")
    cur = conn.execute("INSERT INTO supplier_invoices(no,supplier_id,po_id,amount,created_by,created_at)"
                       " VALUES(?,?,?,?,?,?)", (no or f"SI-{supplier_id}", supplier_id, po_id, amount, actor["id"], now()))
    conn.commit(); audit(conn, actor, "supplierinvoice.create", "supplier_invoice", cur.lastrowid, new={"amount": amount}); conn.commit()
    return cur.lastrowid


# booking messages (internal vs customer-visible) ---------------------------
def post_message(conn, actor, booking_id, visibility, body):
    require(actor, "message.create")
    if visibility not in ("internal", "customer"):
        raise ValidationError("visibility must be internal|customer")
    cur = conn.execute("INSERT INTO booking_messages(booking_id,sender,visibility,body,ts) VALUES(?,?,?,?,?)",
                       (booking_id, "Staff · " + actor["role"], visibility, body, now()))
    conn.commit(); audit(conn, actor, "message.post", "booking_message", cur.lastrowid, new={"visibility": visibility}); conn.commit()
    return cur.lastrowid


def customer_thread(conn, booking_id):
    """Customer-facing conversation — NEVER includes internal notes."""
    return [dict(r) for r in conn.execute(
        "SELECT sender,body,ts FROM booking_messages WHERE booking_id=? AND visibility='customer' ORDER BY id",
        (booking_id,)).fetchall()]


def staff_thread(conn, booking_id):
    return [dict(r) for r in conn.execute(
        "SELECT sender,visibility,body,ts FROM booking_messages WHERE booking_id=? ORDER BY id",
        (booking_id,)).fetchall()]


# system config --------------------------------------------------------------
def set_config(conn, actor, key, value):
    require(actor, "config.write")
    conn.execute("INSERT INTO system_config(key,value,updated_by,updated_at) VALUES(?,?,?,?)"
                 " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by,"
                 " updated_at=excluded.updated_at",
                 (key, str(value), actor["id"], now()))
    audit(conn, actor, "config.set", "system_config", None, new={"key": key})
    conn.commit()


def get_config(conn, key, default=None):
    r = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default
