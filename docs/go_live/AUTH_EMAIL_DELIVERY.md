# Authentication email delivery

Registration codes and password reset tokens use authenticated SMTP over TLS.
The transport sends directly, without persisting plaintext codes in notification
outboxes or recording provider errors in logs. An SMTP acceptance is not proof
of inbox delivery. SMS delivery remains unavailable.

Configure the existing canonical Railway service with a sender and credentials
from an approved mail provider. Do not put credentials in code, issues or chat.

| Variable | Purpose |
| --- | --- |
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
