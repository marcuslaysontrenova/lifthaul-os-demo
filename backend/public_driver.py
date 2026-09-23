"""Public driver intake without inventing a fleet relationship.

An applicant can prove control of an email/mobile contact, but cannot self-assign to a
carrier or self-verify a professional licence.  A permitted operator later sponsors the
application into the canonical ``mkt_drivers`` and ``driver_principals`` records.
"""
from __future__ import annotations

import json
import secrets

import core
import public_provider as otp
import tenant


SCHEMA = """
CREATE TABLE IF NOT EXISTS public_driver_applications(
  id INTEGER PRIMARY KEY, tenant_id INTEGER, user_id INTEGER NOT NULL,
  full_name TEXT NOT NULL, email TEXT, mobile TEXT, base_location TEXT,
  employment_mode TEXT, current_company TEXT, licence_number TEXT NOT NULL,
  licence_class TEXT NOT NULL, licence_expiry TEXT NOT NULL,
  authorized_categories TEXT, years_experience INTEGER DEFAULT 0,
  route_experience TEXT, safety_qualifications TEXT, emergency_contact TEXT,
  consent_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING_CONTACT',
  contact_verified_at TEXT, sponsored_by INTEGER, sponsored_at TEXT,
  carrier_id INTEGER, driver_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS driver_signup(
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  login TEXT NOT NULL, channel TEXT, destination TEXT, code_hash TEXT NOT NULL,
  issued_at TEXT, expires_at TEXT, attempt_count INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'PENDING', verified_at TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_public_driver_status ON public_driver_applications(status,created_at);
CREATE INDEX IF NOT EXISTS idx_driver_signup_login ON driver_signup(login,status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_public_driver_licence ON public_driver_applications(licence_number);
"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def seed(conn, actor=None):
    return


def _actor():
    return otp._service_actor()


def _clean(payload, key, limit=500):
    return str(payload.get(key, "") or "").strip()[:limit]


def submit(conn, payload):
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    full_name = _clean(payload, "full_name", 200)
    email = _clean(payload, "email", 200).lower()
    mobile = _clean(payload, "mobile", 40)
    login = _clean(payload, "username", 200).lower() or email
    password = str(payload.get("password", ""))
    licence_number = _clean(payload, "licence_number", 80).upper()
    licence_class = _clean(payload, "licence_class", 80)
    licence_expiry = _clean(payload, "licence_expiry", 20)
    if not full_name:
        raise core.ValidationError("full name is required")
    if not (email or mobile):
        raise core.ValidationError("an email or mobile number is required")
    if not login:
        raise core.ValidationError("an email or username is required")
    if len(password) < 8:
        raise core.ValidationError("password must be at least 8 characters")
    if not (licence_number and licence_class and licence_expiry):
        raise core.ValidationError("professional licence number, class, and expiry are required")
    duplicate = conn.execute("SELECT 1 FROM public_driver_applications WHERE licence_number=? UNION ALL "
                             "SELECT 1 FROM mkt_drivers WHERE UPPER(licence_number)=? LIMIT 1",
                             (licence_number, licence_number)).fetchone()
    if duplicate:
        raise core.ConflictError("that professional licence number is already registered or under review")
    if payload.get("consent") is not True:
        raise core.ValidationError("privacy notice and application consent must be accepted")
    try:
        years = int(payload.get("years_experience") or 0)
    except (TypeError, ValueError):
        raise core.ValidationError("years of experience must be a whole number")
    if years < 0 or years > 80:
        raise core.ValidationError("years of experience is outside the allowed range")
    categories = payload.get("authorized_categories") or []
    if not isinstance(categories, list):
        raise core.ValidationError("authorized_categories must be a list")

    actor = _actor()
    try:
        uid = core.create_user(conn, login, password, "driver_principal", name=full_name)
    except core.ConflictError:
        raise core.ConflictError("that username/email is already registered")
    conn.execute("UPDATE users SET status='PENDING_DRIVER_REVIEW' WHERE id=?", (uid,))
    now = core.now()
    cur = conn.execute(
        "INSERT INTO public_driver_applications(tenant_id,user_id,full_name,email,mobile,base_location,"
        "employment_mode,current_company,licence_number,licence_class,licence_expiry,authorized_categories,"
        "years_experience,route_experience,safety_qualifications,emergency_contact,consent_at,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING_CONTACT',?,?)",
        (actor.get("tenant_id"), uid, full_name, email or None, mobile or None,
         _clean(payload, "base_location", 300) or None,
         _clean(payload, "employment_mode", 40) or "SEEKING_FLEET",
         _clean(payload, "current_company", 200) or None, licence_number, licence_class,
         licence_expiry, json.dumps(categories), years,
         _clean(payload, "route_experience", 1000) or None,
         _clean(payload, "safety_qualifications", 1000) or None,
         _clean(payload, "emergency_contact", 300) or None, now, now, now))
    app_id = cur.lastrowid
    challenge = _issue(conn, app_id, uid, login, email, mobile)
    core.audit(conn, actor, "PUBLIC_DRIVER_APPLIED", "public_driver_applications", app_id, None,
               {"user_id": uid, "licence_class": licence_class, "status": "PENDING_CONTACT"})
    conn.commit()
    out = {"ref": f"DRV-{app_id}", "application_id": app_id, "challenge_id": challenge["challenge_id"],
           "login": login, "status": "VERIFY_CONTACT", "channel": challenge["channel"],
           "destination": otp._mask(challenge["destination"]), "delivered": challenge["delivered"],
           "delivery_note": challenge["delivery_note"],
           "next": "Verify your contact. LiftHaul operations or a participating fleet must sponsor the application before driver access is activated."}
    if challenge.get("dev_code"):
        out["dev_code"] = challenge["dev_code"]
    return out


def _issue(conn, application_id, user_id, login, email, mobile):
    channel, destination = ("email", email) if email else ("sms", mobile)
    code = f"{secrets.randbelow(1000000):06d}"
    now = core.now()
    conn.execute("UPDATE driver_signup SET status='SUPERSEDED' WHERE user_id=? AND status='PENDING'", (user_id,))
    conn.execute("INSERT INTO driver_signup(application_id,user_id,login,channel,destination,code_hash,"
                 "issued_at,expires_at,attempt_count,status,created_at) VALUES(?,?,?,?,?,?,?,?,0,'PENDING',?)",
                 (application_id, user_id, login, channel, destination, core.hash_pw(code), now,
                  otp._seconds_from(now, otp._CODE_TTL_SECONDS), now))
    row = conn.execute("SELECT id FROM driver_signup WHERE user_id=? AND status='PENDING' ORDER BY id DESC LIMIT 1",
                       (user_id,)).fetchone()
    challenge_id = row["id"]
    delivered, note = otp._deliver_code(conn, channel, destination, code)
    core.audit(conn, _actor(), "DRIVER_SIGNUP_CODE_ISSUED", "driver_signup", challenge_id, None,
               {"channel": channel, "destination": otp._mask(destination), "delivered": delivered})
    out = {"challenge_id": challenge_id, "channel": channel, "destination": destination,
           "delivered": delivered, "delivery_note": note}
    if otp._capture_enabled():
        otp._CODE_CAPTURE[("driver", challenge_id)] = code
    if otp._dev_code_allowed():
        out["dev_code"] = code
    return out


def peek_code(challenge_id):
    if not otp._capture_enabled():
        return None
    return otp._CODE_CAPTURE.get(("driver", int(challenge_id)))


def _pending(conn, payload):
    challenge_id = payload.get("challenge_id")
    login = _clean(payload, "username", 200).lower() or _clean(payload, "login", 200).lower()
    if challenge_id:
        return conn.execute("SELECT * FROM driver_signup WHERE id=?", (challenge_id,)).fetchone()
    if login:
        return conn.execute("SELECT * FROM driver_signup WHERE login=? AND status='PENDING' ORDER BY id DESC LIMIT 1",
                            (login,)).fetchone()
    raise core.ValidationError("challenge_id or username is required")


def verify(conn, payload):
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    code = _clean(payload, "code", 20)
    if not code:
        raise core.ValidationError("the one-time code is required")
    row = _pending(conn, payload)
    if not row or row["status"] != "PENDING":
        raise core.ValidationError("no pending verification found")
    if otp._expired(row["expires_at"]):
        conn.execute("UPDATE driver_signup SET status='EXPIRED' WHERE id=?", (row["id"],))
        conn.commit()
        raise core.ValidationError("the code has expired — request a new one")
    if row["attempt_count"] >= otp._MAX_ATTEMPTS:
        raise core.ValidationError("too many attempts — this verification is locked")
    if not core.verify_pw(code, row["code_hash"]):
        conn.execute("UPDATE driver_signup SET attempt_count=attempt_count+1 WHERE id=?", (row["id"],))
        conn.commit()
        raise core.ValidationError("incorrect code")
    now = core.now()
    conn.execute("UPDATE driver_signup SET status='VERIFIED',verified_at=? WHERE id=?", (now, row["id"]))
    conn.execute("UPDATE public_driver_applications SET status='CONTACT_VERIFIED',contact_verified_at=?,updated_at=? WHERE id=?",
                 (now, now, row["application_id"]))
    core.audit(conn, _actor(), "DRIVER_CONTACT_VERIFIED", "public_driver_applications", row["application_id"], None,
               {"user_id": row["user_id"], "status": "CONTACT_VERIFIED"})
    conn.commit()
    return {"ref": f"DRV-{row['application_id']}", "application_id": row["application_id"],
            "status": "CONTACT_VERIFIED", "login_active": False,
            "message": "Contact verified. Your application is awaiting fleet or LiftHaul operations sponsorship and independent licence review."}


def resend(conn, payload):
    if not isinstance(payload, dict):
        raise core.ValidationError("invalid payload")
    row = _pending(conn, payload)
    if not row or row["status"] not in ("PENDING", "EXPIRED"):
        raise core.ValidationError("no pending verification found")
    ch = _issue(conn, row["application_id"], row["user_id"], row["login"],
                row["destination"] if row["channel"] == "email" else "",
                row["destination"] if row["channel"] == "sms" else "")
    conn.commit()
    out = {"challenge_id": ch["challenge_id"], "channel": ch["channel"],
           "destination": otp._mask(ch["destination"]), "delivered": ch["delivered"],
           "delivery_note": ch["delivery_note"], "status": "VERIFY_CONTACT"}
    if ch.get("dev_code"):
        out["dev_code"] = ch["dev_code"]
    return out


def list_applications(conn, actor, status=None):
    core.require(actor, "marketplace.driver.manage")
    frag, scoped = tenant.predicate(actor)
    sql, args = "SELECT * FROM public_driver_applications WHERE 1=1" + frag, list(scoped)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def sponsor(conn, actor, application_id, carrier_id):
    """Create canonical driver + principal after contact verification and explicit carrier selection."""
    core.require(actor, "marketplace.driver.manage")
    app = conn.execute("SELECT * FROM public_driver_applications WHERE id=?", (application_id,)).fetchone()
    if not app:
        raise core.NotFoundError("driver application not found")
    tenant.guard(actor, app)
    if app["driver_id"]:
        return {"application_id": application_id, "driver_id": app["driver_id"], "status": app["status"]}
    if app["status"] != "CONTACT_VERIFIED":
        raise core.ValidationError("driver contact must be verified before sponsorship")
    import marketplace_onboarding as mo
    import driver_app
    did = mo.register_driver(conn, actor, int(carrier_id), app["full_name"],
                             licence_number=app["licence_number"], licence_class=app["licence_class"],
                             licence_expiry=app["licence_expiry"],
                             authorized_categories=json.loads(app["authorized_categories"] or "[]"),
                             safety_qualifications=app["safety_qualifications"],
                             route_experience=app["route_experience"], emergency_contact=app["emergency_contact"])
    driver_app.bind_principal(conn, actor, app["user_id"], did)
    now = core.now()
    conn.execute("UPDATE users SET status='ACTIVE' WHERE id=?", (app["user_id"],))
    conn.execute("UPDATE public_driver_applications SET status='SPONSORED',carrier_id=?,driver_id=?,"
                 "sponsored_by=?,sponsored_at=?,updated_at=? WHERE id=?",
                 (carrier_id, did, actor["id"], now, now, application_id))
    core.audit(conn, actor, "PUBLIC_DRIVER_SPONSORED", "public_driver_applications", application_id, None,
               {"carrier_id": carrier_id, "driver_id": did, "user_id": app["user_id"]})
    conn.commit()
    return {"application_id": application_id, "driver_id": did, "carrier_id": carrier_id,
            "status": "SPONSORED", "compliance_status": "APPLICATION"}
