"""RGO OS backend — quotation PDF generation (§ Phase-2.1).

A dependency-free, valid PDF writer (so it runs and tests pass without reportlab)
plus quotation-PDF generation that snapshots the exact quotation version, stores
the document via a pluggable DocumentStore, records the reference, and is
immutable: a later revision creates a new version + new PDF; the historical PDF is
never modified. A real deployment swaps DocumentStore for S3/local disk and can
swap the writer for reportlab/WeasyPrint — callers don't change.
"""
from __future__ import annotations

import base64
import hashlib

import core
from core import require, audit, now, NotFoundError, ForbiddenError

core.PERMISSIONS["estimator"] |= {"document.create", "document.read"}
core.PERMISSIONS["approver"] |= {"document.read"}
core.PERMISSIONS["finance"] |= {"document.read"}


# --------------------------------------------------------------------------- #
# Minimal valid PDF writer (single page, Helvetica text)
# --------------------------------------------------------------------------- #
def render_pdf(text_lines) -> bytes:
    def esc(s):
        return str(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    parts = ["BT", "/F1 11 Tf", "40 760 Td", "15 TL"]
    for i, ln in enumerate(text_lines):
        parts.append(("(%s) Tj" if i == 0 else "T* (%s) Tj") % esc(ln))
    parts.append("ET")
    content = ("\n".join(parts)).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode() + o + b"\nendobj\n"
    xref_pos = len(out)
    out += ("xref\n0 %d\n" % (len(objs) + 1)).encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
            % (len(objs) + 1, xref_pos)).encode()
    return out


# --------------------------------------------------------------------------- #
# Document store (swap for S3/local disk in prod)
# --------------------------------------------------------------------------- #
class DocumentStore:
    def put(self, ref: str, data: bytes) -> str:
        raise NotImplementedError

    def get(self, ref: str) -> bytes:
        raise NotImplementedError


class MemStore(DocumentStore):
    def __init__(self):
        self._d = {}

    def put(self, ref, data):
        self._d[ref] = data
        return ref

    def get(self, ref):
        if ref not in self._d:
            raise NotFoundError("document not found in store")
        return self._d[ref]


class DbStore(DocumentStore):
    """Durable, immutable document bytes stored in the system-of-record DB."""
    def __init__(self, conn):
        self.conn = conn

    def put(self, ref, data):
        raw = bytes(data)
        checksum = hashlib.sha256(raw).hexdigest()
        encoded = base64.b64encode(raw).decode("ascii")
        self.conn.execute(
            "INSERT INTO document_contents(storage_ref,content_base64,checksum,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(storage_ref) DO NOTHING", (ref, encoded, checksum, now()))
        row = self.conn.execute(
            "SELECT checksum FROM document_contents WHERE storage_ref=?", (ref,)).fetchone()
        if not row or row["checksum"] != checksum:
            self.conn.rollback()
            raise ValueError("document reference already exists with different immutable content")
        self.conn.commit()
        return ref

    def get(self, ref):
        row = self.conn.execute(
            "SELECT content_base64 FROM document_contents WHERE storage_ref=?", (ref,)).fetchone()
        if not row:
            raise NotFoundError("document not found in store")
        return base64.b64decode(row["content_base64"].encode("ascii"), validate=True)


def _peso(n):
    return "PHP " + format(round(n or 0), ",d")


# --------------------------------------------------------------------------- #
# Quotation PDF
# --------------------------------------------------------------------------- #
def generate_quotation_pdf(conn, actor, quotation_id, store: DocumentStore,
                           company="RGO Machine Rigging Services"):
    require(actor, "document.create")
    q = conn.execute("SELECT * FROM quotations WHERE id=?", (quotation_id,)).fetchone()
    if not q:
        raise NotFoundError("quotation not found")
    import tenant; tenant.guard(actor, q)                 # no cross-tenant PDF gen/download (404 no-leak)
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (q["booking_id"],)).fetchone()
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (b["customer_id"],)).fetchone()
    lines = conn.execute("SELECT * FROM quotation_lines WHERE quotation_id=? ORDER BY id", (quotation_id,)).fetchall()
    txt = [
        company, "QUOTATION " + q["no"] + "  (version " + str(q["version"]) + ")", "",
        "Customer: " + (cust["name"] if cust else "-"),
        "Booking:  " + (b["ref"] if b else "-"),
        "Status:   " + q["status"] + "    Issued: " + (q["created_at"] or "-")[:10], "",
        "Line items:",
    ]
    for l in lines:
        txt.append("  - %s  x%s x%sd  @ %s = %s" % (
            l["description"] or l["kind"] or "item", l["qty"], l["days"], _peso(l["rate"]),
            _peso(l["rate"] * (l["qty"] or 1) * (l["days"] or 1))))
    txt += [
        "", "Subtotal:      " + _peso(q["subtotal"]),
        "Discount (%s%%): -%s" % (q["discount_pct"], _peso(q["discount"])),
        "VAT:           " + _peso(q["tax"]),
        "TOTAL:         " + _peso(q["total"]),
        "Downpayment (%s%%): %s" % (q["dp_pct"], _peso(q["dp_amount"])),
        "Balance:       " + _peso(q["balance"]), "",
        "Terms: 50%% mobilization on lift date. Standby billed hourly.",
        "Exclusions: permits and escorts unless stated. Quotation subject to site conditions.",
    ]
    pdf = render_pdf(txt)
    # Include the immutable quotation id so two tenants using the same displayed
    # quotation number can never collide in the shared document store.
    ref = "quote_%s_%s_v%d.pdf" % (quotation_id, q["no"], q["version"])
    store.put(ref, pdf)
    cur = conn.execute(
        "INSERT INTO documents(entity,entity_id,filename,content_type,size,category,scan_status,"
        "storage_ref,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("quotation", quotation_id, ref, "application/pdf", len(pdf), "quotation", "clean", ref, actor["id"], now()))
    conn.commit()
    audit(conn, actor, "quotation.pdf", "quotation", quotation_id, new={"ref": ref, "size": len(pdf), "version": q["version"]})
    conn.commit()
    return {"doc_id": cur.lastrowid, "ref": ref, "size": len(pdf), "bytes": pdf}


def get_quotation_pdf(conn, actor, quotation_id, store: DocumentStore):
    q = conn.execute("SELECT * FROM quotations WHERE id=?", (quotation_id,)).fetchone()
    if not q:
        raise NotFoundError("quotation not found")
    import tenant; tenant.guard(actor, q)                 # no cross-tenant PDF gen/download (404 no-leak)
    b = conn.execute("SELECT customer_id FROM bookings WHERE id=?", (q["booking_id"],)).fetchone()
    if actor["role"] == "customer":
        require(actor, "self.quotation.read")
        if actor.get("customer_id") != b["customer_id"]:
            raise ForbiddenError("customers may access only their own quotation")
    else:
        require(actor, "document.read")
    doc = conn.execute("SELECT storage_ref FROM documents WHERE entity='quotation' AND entity_id=?"
                       " ORDER BY id DESC LIMIT 1", (quotation_id,)).fetchone()
    if not doc:
        raise NotFoundError("no PDF generated for this quotation")
    return store.get(doc["storage_ref"])
