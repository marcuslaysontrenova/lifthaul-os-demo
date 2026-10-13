"""Direct auth-email transport. Secrets and one-time codes never enter the outbox.

Provider acceptance is not proof of inbox delivery. Resend HTTPS works on Railway
Hobby; authenticated TLS SMTP is available for hosts that permit it.
"""
import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import parseaddr


def _address(value):
    value = str(value or "").strip()
    if "\r" in value or "\n" in value:
        raise ValueError("Invalid mail address")
    _, addr = parseaddr(value)
    if addr != value or addr.count("@") != 1 or not all(addr.split("@")):
        raise ValueError("Invalid mail address")
    return addr


def _config():
    host = os.environ.get("LH_AUTH_SMTP_HOST", "").strip()
    username = os.environ.get("LH_AUTH_SMTP_USERNAME", "")
    password = os.environ.get("LH_AUTH_SMTP_PASSWORD", "")
    sender = _address(os.environ.get("LH_AUTH_EMAIL_FROM", ""))
    mode = os.environ.get("LH_AUTH_SMTP_SECURITY", "starttls").strip().lower()
    port = int(os.environ.get("LH_AUTH_SMTP_PORT", "465" if mode == "ssl" else "587"))
    if not host or not username or not password or mode not in {"ssl", "starttls"}:
        raise ValueError("Secure auth email configuration missing")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid SMTP port")
    return host, port, username, password, sender, mode


def configured():
    try:
        provider = os.environ.get("LH_AUTH_EMAIL_PROVIDER", "smtp").strip().lower()
        if provider == "resend":
            _resend_config()
        elif provider == "smtp":
            _config()
        else:
            return False
        return True
    except (ValueError, TypeError):
        return False


def _resend_config():
    key = os.environ.get("LH_AUTH_RESEND_API_KEY", "").strip()
    sender = _address(os.environ.get("LH_AUTH_EMAIL_FROM", ""))
    if not key or "\r" in key or "\n" in key:
        raise ValueError("Protected Resend API key missing")
    return key, sender


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send_resend(destination, subject, body):
    key, sender = _resend_config()
    recipient = _address(destination)
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": sender, "to": [recipient], "subject": subject, "text": body}).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "LiftHaul-Auth-Email/1.0"},
        method="POST")
    # Pin the HTTPS destination. Never forward the key or auth message on redirects.
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=10) as response:
        if response.status != 200:
            return False
        result = json.loads(response.read(8192))
        return isinstance(result, dict) and isinstance(result.get("id"), str) and bool(result["id"].strip())


def send(destination, subject, body):
    """Return True only after the recipient is accepted; never log exceptions/PII."""
    try:
        provider = os.environ.get("LH_AUTH_EMAIL_PROVIDER", "smtp").strip().lower()
        if provider == "resend":
            return _send_resend(destination, subject, body)
        if provider != "smtp":
            return False
        host, port, username, password, sender, mode = _config()
        recipient = _address(destination)
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = sender, recipient, subject
        message.set_content(body)
        context = ssl.create_default_context()
        if mode == "ssl":
            transport = smtplib.SMTP_SSL(host, port, timeout=10, context=context)
        else:
            transport = smtplib.SMTP(host, port, timeout=10)
        with transport as smtp:
            if mode == "starttls":
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(username, password)
            return not smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
    except Exception:
        # Provider errors can contain passwords, recipients or message content.
        return False


def send_code(destination, code):
    return send(destination, "LiftHaul contact verification code",
                f"Your LiftHaul verification code is {code}. It expires in 15 minutes.\n"
                "Do not share this code. Ignore this email if you did not request it.")


def send_reset(destination, token):
    return send(destination, "LiftHaul password reset",
                f"Your single-use LiftHaul password reset token is:\n{token}\n\n"
                "Enter it in the password recovery form at https://www.lifthaul.com.ph/client.html.\n"
                "It expires in 30 minutes. Do not share it. Ignore this email if you did not request it.")
