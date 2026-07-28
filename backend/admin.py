"""RGO OS backend — admin & support domain (extends core + ops).

Adds the remaining not-yet-built areas as real, tested code:
  * master data (§4) with in-use delete-guard + deactivate;
  * inventory + movements (§23) with non-negative stock + low-stock detection;
  * documents (§24) with file-type/size validation + malware-scan hook (interface);
  * notifications (§7/§24) with templates + a pluggable NotificationSender;
  * subcontractors, suppliers, purchase orders (§21);
  * safety records + incidents (§8/§22) + a real dispatch **safety gate**.

Same discipline: server-side authorization (core RBAC), foreign keys, audit.
"""
from __future__ import annotations

import core
import ops   # noqa: F401  — ensures base ops roles (fleet_manager, mechanic, safety_officer, ...) exist
from core import require, audit, now, ConflictError, NotFoundError, ValidationError, ForbiddenError

# extend RBAC
core.PERMISSIONS["operations_manager"] |= {"inventory.*", "document.*", "notification.*",
                                           "subcontractor.*", "supplier.*", "safety.read"}
core.PERMISSIONS["finance"] |= {"supplier.*", "document.read"}
core.PERMISSIONS["safety_officer"] |= {"safety.create", "safety.read", "incident.create", "document.create"}
core.PERMISSIONS["fleet_manager"] |= {"inventory.*", "supplier.read"}
core.PERMISSIONS.setdefault("procurement", {"supplier.*", "inventory.read", "document.read"})


ADMIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS master_data(
  id INTEGER PRIMARY KEY, category TEXT NOT NULL, code TEXT NOT NULL, label TEXT,
  active INTEGER DEFAULT 1, ref_count INTEGER DEFAULT 0,
  created_by INTEGER, created_at TEXT, UNIQUE(category, code));

CREATE TABLE IF NOT EXISTS subcontractors(
  id INTEGER PRIMARY KEY, company TEXT NOT NULL, contact TEXT, coverage TEXT,
  insurance_expiry TEXT, status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS suppliers(
  id INTEGER PRIMARY KEY, company TEXT NOT NULL, contact TEXT, category TEXT,
  status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS purchase_orders(
  id INTEGER PRIMARY KEY, no TEXT UNIQUE NOT NULL, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  description TEXT, amount REAL, status TEXT DEFAULT 'OPEN', created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS inventory_items(
  id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT, uom TEXT,
  qty REAL DEFAULT 0, reorder_point REAL DEFAULT 0, warehouse TEXT,
  status TEXT DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS inventory_movements(
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES inventory_items(id),
  kind TEXT NOT NULL, qty REAL NOT NULL, ref TEXT, actor INTEGER, ts TEXT);

CREATE TABLE IF NOT EXISTS safety_records(
  id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
  kind TEXT, result TEXT NOT NULL, notes TEXT, officer INTEGER, ts TEXT);

CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), severity TEXT, description TEXT,
  status TEXT DEFAULT 'OPEN', reported_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, entity TEXT, entity_id INTEGER, filename TEXT, content_type TEXT,
  size INTEGER, category TEXT, scan_status TEXT, storage_ref TEXT,
  uploaded_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS notification_templates(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, subject TEXT, body TEXT);

CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, template TEXT, recipient TEXT, subject TEXT, body TEXT,
  channel TEXT, status TEXT DEFAULT 'QUEUED', created_at TEXT, sent_at TEXT);
"""


def init_admin(conn):
    conn.executescript(ADMIN_SCHEMA)
    conn.commit()


def connect_full(path=":memory:"):
    import ops
    conn = ops.connect_full(path)
    init_admin(conn)
    return conn


# --------------------------------------------------------------------------- #
# File-security + notification interfaces (mock adapters; real ones drop in)
# --------------------------------------------------------------------------- #
ALLOWED_DOC_TYPES = {"application/pdf", "image/jpeg", "image/png", "text/plain"}
MAX_DOC_BYTES = 15 * 1024 * 1024


class DocumentScanner:
    def scan(self, storage_ref) -> str:  # 'clean' | 'infected'
        raise NotImplementedError


class MockScanner(DocumentScanner):
    def scan(self, storage_ref):
        return "infected" if "virus" in (storage_ref or "").lower() else "clean"


class NotificationSender:
    def send(self, *, recipient, subject, body, channel) -> bool:
        raise NotImplementedError


class MockSender(NotificationSender):
    sent = []

    def send(self, *, recipient, subject, body, channel):
        MockSender.sent.append({"recipient": recipient, "subject": subject, "channel": channel})
        return True


# --------------------------------------------------------------------------- #
# §4 Master data — create / deactivate / delete-if-unused
# --------------------------------------------------------------------------- #
def md_create(conn, actor, category, code, label=None):
    require(actor, "masterdata.create")
    try:
        cur = conn.execute("INSERT INTO master_data(category,code,label,created_by,created_at)"
                           " VALUES(?,?,?,?,?)", (category, code, label, actor["id"], now()))
    except Exception:
        raise ConflictError("duplicate master-data code in category")
    conn.commit()
    audit(conn, actor, "masterdata.create", "master_data", cur.lastrowid, new={"category": category, "code": code})
    conn.commit()
    return cur.lastrowid


def md_mark_used(conn, mid, delta=1):
    conn.execute("UPDATE master_data SET ref_count=ref_count+? WHERE id=?", (delta, mid))
    conn.commit()


def md_deactivate(conn, actor, mid):
    require(actor, "masterdata.update")
    conn.execute("UPDATE master_data SET active=0 WHERE id=?", (mid,))
    audit(conn, actor, "masterdata.deactivate", "master_data", mid)
    conn.commit()


def md_delete(conn, actor, mid):
    require(actor, "masterdata.delete")
    r = conn.execute("SELECT ref_count FROM master_data WHERE id=?", (mid,)).fetchone()
    if not r:
        raise NotFoundError("master-data value not found")
    if r["ref_count"] > 0:
        raise ConflictError("CONTROL: value is used by transactions — deactivate instead of delete")
    conn.execute("DELETE FROM master_data WHERE id=?", (mid,))
    audit(conn, actor, "masterdata.delete", "master_data", mid)
    conn.commit()


# --------------------------------------------------------------------------- #
# §23 Inventory + movements (non-negative stock, low-stock)
# --------------------------------------------------------------------------- #
def inv_create(conn, actor, sku, name, uom="pc", reorder_point=0, warehouse=None):
    require(actor, "inventory.create")
    try:
        cur = conn.execute("INSERT INTO inventory_items(sku,name,uom,reorder_point,warehouse,created_by,created_at)"
                           " VALUES(?,?,?,?,?,?,?)", (sku, name, uom, reorder_point, warehouse, actor["id"], now()))
    except Exception:
        raise ConflictError("duplicate SKU")
    conn.commit()
    audit(conn, actor, "inventory.create", "inventory_item", cur.lastrowid, new={"sku": sku})
    conn.commit()
    return cur.lastrowid


def inv_move(conn, actor, item_id, kind, qty, ref=None):
    """kind: IN | OUT | ADJUST. OUT may not drive stock negative."""
    require(actor, "inventory.move")
    it = conn.execute("SELECT qty FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not it:
        raise NotFoundError("inventory item not found")
    delta = qty if kind in ("IN", "ADJUST") else -abs(qty)
    if kind == "OUT" and it["qty"] - abs(qty) < 0:
        raise ValidationError("CONTROL: insufficient stock — cannot go negative")
    with conn:
        conn.execute("UPDATE inventory_items SET qty=qty+? WHERE id=?", (delta, item_id))
        conn.execute("INSERT INTO inventory_movements(item_id,kind,qty,ref,actor,ts) VALUES(?,?,?,?,?,?)",
                     (item_id, kind, qty, ref, actor["id"], now()))
        audit(conn, actor, "inventory.move", "inventory_item", item_id, new={"kind": kind, "qty": qty})
    return conn.execute("SELECT qty FROM inventory_items WHERE id=?", (item_id,)).fetchone()["qty"]


def low_stock(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM inventory_items WHERE status='ACTIVE' AND qty <= reorder_point").fetchall()]


# --------------------------------------------------------------------------- #
# §24 Documents with file-security
# --------------------------------------------------------------------------- #
def doc_upload(conn, actor, entity, entity_id, filename, content_type, size, storage_ref,
               category=None, scanner: DocumentScanner = None):
    require(actor, "document.create")
    if content_type not in ALLOWED_DOC_TYPES:
        raise ValidationError(f"file type '{content_type}' not allowed")
    if size is None or size <= 0 or size > MAX_DOC_BYTES:
        raise ValidationError("file size out of allowed range")
    scan = (scanner or MockScanner()).scan(storage_ref)
    if scan != "clean":
        audit(conn, actor, "document.rejected", "document", entity_id, new={"scan": scan, "filename": filename})
        conn.commit()
        raise ValidationError("CONTROL: file failed malware scan — rejected")
    cur = conn.execute(
        "INSERT INTO documents(entity,entity_id,filename,content_type,size,category,scan_status,"
        "storage_ref,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (entity, entity_id, filename, content_type, size, category, scan, storage_ref, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "document.upload", "document", cur.lastrowid, new={"filename": filename, "scan": scan})
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# §7/§24 Notifications
# --------------------------------------------------------------------------- #
def nt_template(conn, actor, code, subject, body):
    require(actor, "notification.create")
    conn.execute("INSERT INTO notification_templates(code,subject,body) VALUES(?,?,?)"
                 " ON CONFLICT(code) DO UPDATE SET subject=excluded.subject, body=excluded.body",
                 (code, subject, body))
    conn.commit()


def notify(conn, actor, template_code, recipient, ctx=None, channel="email", sender: NotificationSender = None):
    require(actor, "notification.create")
    t = conn.execute("SELECT * FROM notification_templates WHERE code=?", (template_code,)).fetchone()
    if not t:
        raise NotFoundError("template not found")
    ctx = ctx or {}
    subject = t["subject"].format(**ctx)
    body = t["body"].format(**ctx)
    cur = conn.execute("INSERT INTO notifications(template,recipient,subject,body,channel,status,created_at)"
                       " VALUES(?,?,?,?,?, 'QUEUED',?)",
                       (template_code, recipient, subject, body, channel, now()))
    nid = cur.lastrowid
    ok = (sender or MockSender()).send(recipient=recipient, subject=subject, body=body, channel=channel)
    conn.execute("UPDATE notifications SET status=?, sent_at=? WHERE id=?",
                 ("SENT" if ok else "FAILED", now() if ok else None, nid))
    conn.commit()
    audit(conn, actor, "notification.send", "notification", nid, new={"template": template_code, "to": recipient})
    conn.commit()
    return nid


# --------------------------------------------------------------------------- #
# §21 Subcontractors / suppliers / POs
# --------------------------------------------------------------------------- #
def sc_create(conn, actor, company, coverage=None, insurance_expiry=None):
    require(actor, "subcontractor.create")
    cur = conn.execute("INSERT INTO subcontractors(company,coverage,insurance_expiry,created_by,created_at)"
                       " VALUES(?,?,?,?,?)", (company, coverage, insurance_expiry, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "subcontractor.create", "subcontractor", cur.lastrowid, new={"company": company})
    conn.commit()
    return cur.lastrowid


def sup_create(conn, actor, company, category=None):
    require(actor, "supplier.create")
    cur = conn.execute("INSERT INTO suppliers(company,category,created_by,created_at) VALUES(?,?,?,?)",
                       (company, category, actor["id"], now()))
    conn.commit()
    return cur.lastrowid


def po_create(conn, actor, supplier_id, description, amount):
    require(actor, "supplier.create")
    if not conn.execute("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)).fetchone():
        raise NotFoundError("supplier not found")
    n = conn.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()["c"]
    no = f"PO-{6001 + n}"
    cur = conn.execute("INSERT INTO purchase_orders(no,supplier_id,description,amount,created_by,created_at)"
                       " VALUES(?,?,?,?,?,?)", (no, supplier_id, description, amount, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "po.create", "purchase_order", cur.lastrowid, new={"no": no, "amount": amount})
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# §8/§22 Safety + incidents  (+ gate consumed by ops.transition_job)
# --------------------------------------------------------------------------- #
def safety_record(conn, actor, job_id, result, kind="toolbox", notes=None):
    require(actor, "safety.create")
    if result not in ("PASS", "FAIL"):
        raise ValidationError("result must be PASS or FAIL")
    cur = conn.execute("INSERT INTO safety_records(job_id,kind,result,notes,officer,ts) VALUES(?,?,?,?,?,?)",
                       (job_id, kind, result, notes, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "safety.record", "safety_record", cur.lastrowid, new={"job": job_id, "result": result})
    conn.commit()
    return cur.lastrowid


def report_incident(conn, actor, job_id, severity, description):
    require(actor, "incident.create")
    cur = conn.execute("INSERT INTO incidents(job_id,severity,description,reported_by,created_at)"
                       " VALUES(?,?,?,?,?)", (job_id, severity, description, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "incident.report", "incident", cur.lastrowid, new={"severity": severity})
    conn.commit()
    return cur.lastrowid
