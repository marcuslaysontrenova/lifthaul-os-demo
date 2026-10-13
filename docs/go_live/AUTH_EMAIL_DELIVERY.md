# Authentication email delivery

Registration codes and password reset tokens support Resend HTTPS and authenticated SMTP over TLS.
The transport sends directly, without persisting plaintext codes in notification
outboxes or recording provider errors in logs. A provider acceptance is not proof
of inbox delivery. SMS delivery remains unavailable.

Railway Free, Trial and Hobby plans block SMTP. Use the Resend HTTPS adapter on
the current Hobby plan; SMTP is an alternative only on hosts/plans that permit it.
Source: https://docs.railway.com/networking/outbound-networking

The owner selected `inquiries@trenovatech.ai` as the interim sender. Public MX
records route `trenovatech.ai` to `mx1.privateemail.com` and `mx2.privateemail.com`.
Mailbox existence and sending authority must still be verified. Receiving mail
can stay with Private Email while the HTTPS provider sends authentication mail.

## Railway Hobby setup

1. Create or sign in to the owner's Resend account. The currently published free
   tier permits 3,000 emails/month and 100/day. Keep paid upgrades and paid
   overages off; this change does not purchase a plan.
2. Verify `trenovatech.ai` using exactly the DNS records generated in the Resend
   dashboard. Preserve existing website records and Private Email MX routing.
   Do not invent DKIM values or replace the existing root mail-routing MX records.
3. Create a send-only API key restricted to this verified domain. Put it directly
   into protected Railway configuration; never into code, issues, screenshots or chat.
4. Configure the existing canonical service with the following variables:

| Variable | Value |
| --- | --- |
| `LH_AUTH_EMAIL_PROVIDER` | `resend` |
| `LH_AUTH_RESEND_API_KEY` | Protected send-only API key |
| `LH_AUTH_EMAIL_FROM` | `inquiries@trenovatech.ai`, after domain verification |

Only a successful HTTPS response containing a provider message ID counts as
accepted. Quota errors, invalid keys, unverified domains, redirects, missing
configuration and invalid responses fail honestly. No redirects may forward the
API key or authentication message. Receipt in the intended inbox remains a
separate acceptance check. SMS delivery remains unavailable.

Provider references:
- https://resend.com/pricing
- https://resend.com/docs/add-a-domain
- https://resend.com/docs/api-reference/emails/send-email

## Optional SMTP setup for supported hosts

| Variable | Purpose |
| --- | --- |
| `LH_AUTH_EMAIL_PROVIDER` | `smtp` (default); do not use on Railway Hobby |
| `LH_AUTH_SMTP_HOST` | Approved provider SMTP hostname |
| `LH_AUTH_SMTP_PORT` | 587 for STARTTLS, or 465 for implicit TLS by default |
| `LH_AUTH_SMTP_SECURITY` | `starttls` (default) or `ssl`; plaintext rejected |
| `LH_AUTH_SMTP_USERNAME` | Protected provider login |
| `LH_AUTH_SMTP_PASSWORD` | Protected provider password |
| `LH_AUTH_EMAIL_FROM` | Verified sender email address |

No variable is configured by this change. Missing configuration, TLS errors,
authentication failures and recipient refusals never produce a delivery success.
Driver/provider accounts remain pending until their code is verified.
Client recovery responses remain identical for known and unknown accounts;
production tokens never appear in API responses even if requested internally.
Missing or unknown `APP_ENV` takes the production posture and cannot seed a demo
client account. Development token display requires an explicit development/test
environment.

After provider configuration, verify receipt in an operator-controlled inbox,
then complete registration, login, password recovery, expiry, single-use and
session revocation checks. Record non-secret evidence in issue #20. Offline
transport tests establish behavior, not real provider/inbox availability.
This does not provision the scoped production QA account or payment readiness.
