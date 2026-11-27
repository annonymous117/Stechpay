# Deployment and Paystack test payments

Hosted testing uses `APP_ENV=staging`, `PAYSTACK_MODE=test` and
`ALLOW_LIVE_PAYMENTS=False`. Paystack test keys work with all verification and
security checks enabled. Mock approvals are not supported. Live payments require
an explicit production rollout and PostgreSQL; changing a key alone cannot enable them.

Use `environment.example` to prepare `/etc/stechpay.env`, owned by `root:www-data`
with mode `640`. Generate a unique random Django secret; replace every placeholder.
Set the correct public HTTPS `FRONTEND_URL` and trusted CSRF origin. The browser
must use that canonical frontend origin so its checkout ownership cookie returns
with the Paystack callback. Never commit actual credentials.

Before enabling deployment:

1. Verify the existing PostgreSQL connection and dedicated application role/database;
   provision these only for a new host. Keep the existing hosted data. The application
   role should not be a database superuser. For a remote database, use
   `POSTGRES_SSLMODE=verify-full` and a trusted `PGSSLROOTCERT`.
2. Provision the TLS certificate. Replace `example.com`, the public hostname and
   certificate paths in `nginx/stechpay.conf`; then install it in Nginx. The template
   assumes exactly one trusted Nginx proxy (`TRUSTED_PROXY_COUNT=1`) and overwrites
   caller-supplied forwarding headers. Review this before adding another proxy/CDN.
3. Configure working SMTP and a deliverable email address for each administrator.
   Hosted deployments require a password plus an emailed one-time code. Reset any
   reused exposed password with `python manage.py changepassword <username>`; the
   old exposed value is blocked. The first security migration logs out old sessions.
4. Install `stechpay.service`, `stechpay-payments.service` and
   `stechpay-payments.timer` into `/etc/systemd/system/`, then run
   `systemctl daemon-reload`. Enable the timer after migrations. It processes
   webhook events, reconciles payments and sends queued emails once per minute.
5. Create a private backup directory outside the web root, writable by the
   deployment user, and set `STECHPAY_BACKUP_DIR`. Install `pg_dump` matching the
   server major version. Restrict backup access and define retention/restore drills.
6. Run `python manage.py check --deploy --fail-level WARNING`. It deliberately
   rejects SQLite, insecure hosted settings, missing/mismatched payment credentials,
   placeholder Django secrets and missing SMTP/MFA configuration.

Run `bash deployment/deploy.sh <tested-40-character-commit-sha>` from the configured
checkout. It refuses tracked local modifications, checks out that tested revision,
installs/builds dependencies, verifies configuration and takes a PostgreSQL backup
before migrations. It then restarts the app and enables the payment timer. The
systemd service also repeats the deployment check before starting. These files
have not been installed on a remote host by the local remediation task.

CI matches the VM's Python 3.12, PostgreSQL 16 and Node 20 runtime. It runs SQLite
and PostgreSQL tests, dependency audits, a targeted credential guard,
Bandit, frontend lint and the production build. Configure the GitHub `production`
environment with deployment protection, and provide `PROD_HOST`, `PROD_USERNAME`,
`PROD_PORT`, `PROD_SSH_KEY` and `PROD_KNOWN_HOSTS`. Obtain the SSH host key through a
trusted channel; do not replace host verification with an unchecked runtime scan.
The workflow fetches the deployment script from the exact tested Git revision.

Install `ci-entrypoint.sh` root-owned and executable at
`/usr/local/bin/stechpay-ci-entrypoint`. Use a dedicated CI public key in the
deployment user's `authorized_keys`, prefixed with
`restrict,command="/usr/local/bin/stechpay-ci-entrypoint"`. The workflow sends
`stechpay-deploy <sha>`; the wrapper allows only this command and the read-only
`stechpay-check` diagnostic, fetches the trusted main branch, and rejects the old
deployment script without backup gates. Keep deployment secrets in the
`production` environment, restricted to the `main` branch.

The existing VM has a trusted IP-address certificate at
`/etc/letsencrypt/live/84.12.126.79/`. The Nginx HTTP ACME challenge location must
remain reachable. `stechpay-certbot.timer` checks renewal twice daily using the
isolated `/opt/stechpay-certbot` installation; its deploy hook validates and reloads
Nginx. These short-lived certificates require working automatic renewal.

`stechpay-backup.timer` runs daily at 02:30 UTC (03:30 Lagos), with up to ten minutes
of jitter. It writes private PostgreSQL archives to `/var/backups/stechpay` using
`backup.sh`, installed as `/usr/local/bin/stechpay-backup`. The initial scheduled
backup was successfully restored into an isolated database. These are on-VM
backups; establish off-VM storage and a retention policy separately. No automatic
backup deletion is configured.

For optional off-VM uploads, install `upload_backup.py` root-owned at
`/usr/local/lib/stechpay/upload_backup.py` and OCI tooling separately under
`/opt/stechpay-oci`. Set `STECHPAY_BACKUP_BUCKET`, `STECHPAY_BACKUP_NAMESPACE`
and `STECHPAY_BACKUP_REGION` in root-only `/etc/stechpay-backup.env`; the backup
unit loads that file if it exists. Configure a VM instance-principal policy
limited to `OBJECT_CREATE` in the intended private bucket. Uploads use unique
names, refuse overwrite, verify Oracle's checksum and save private upload
receipts. A failed transfer retains the local archive and fails the job. The
100 MiB per-archive limit is a guard for small/free-tier deployments; review
capacity and retention before increasing it. Cloud retention and a downloaded
archive restore drill are separate operating steps.

## Moving existing local test data to PostgreSQL

Keep a verified SQLite backup first. Run the security migrations on SQLite, then
export data to a private file outside the frontend web root while SQLite is still
configured:

```sh
python manage.py dumpdata --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.permission --exclude sessions \
  --output /private/path/stechpay-transfer.json
```

Configure a new empty PostgreSQL database in the environment, run `manage.py migrate`,
then `manage.py loaddata /private/path/stechpay-transfer.json`. Verify payment counts,
amounts, permissions and relationships against the backup before switching traffic.
Treat the transfer file as sensitive and remove it according to the backup policy
after verification. Do not overwrite an existing populated PostgreSQL database.

Existing payments are retained as test records, as confirmed for this rollout.
Historical synthetic mock approvals are invalidated and audited. Other historical
successes must be reconciled with Paystack before new receipts are issued. Guest
browser ownership is not assigned retroactively: authorized bursary staff can
recover old receipts. Never use matric numbers or caller-supplied email as proof
of ownership.

## Development and operations

Administrator sign-in uses `/sign-in` followed by `/sign-in/verify`. The email
code is bound to the server session, expires after five minutes, and permits at
most five verification attempts. Resends have a 60-second cooldown and a cap of
three; they replace the code without extending its expiry or resetting attempts.
Password, code verification and resends have separate database-backed account/IP
budgets per five minutes: 5/20, 20/40 and 6/20 respectively. The per-challenge code
attempt limit and resend cooldown still apply. Refreshing the code page reads the
current challenge without sending another email. Public navigation has no admin
button; staff should bookmark `/sign-in`. Authorization still protects every
admin endpoint, including Django's `/admin/` interface.

Student receipt recovery starts at `/receipts`, followed by `/receipts/verify`.
Confirmation emails link to the reference-prefilled recovery form. The request
queues an email job without revealing whether the reference exists. The existing
payment worker sends six-digit codes only to an eligible payment's recorded
mailbox, normally on its next scheduled run. Challenges expire after ten minutes,
allow five code attempts and three resends with a 60-second cooldown. A successful
code grants receipt-only access in that browser for ten minutes; it does not
authenticate a student/admin or grant access to other payments. Reprinting retains
the original receipt number and re-verifies the payment with Paystack. If email
access is unavailable, administration can help with recovery.

General API throttling now uses atomic PostgreSQL counters shared across workers
(default 120/minute per anonymous IP, 300/minute per authenticated user), in addition
to Nginx limits. Recovery request/resend/confirmation budgets per IP are 10/10/30
per ten minutes; email delivery is additionally capped at six requests per reference
and eight delivery attempts per mailbox per hour. These limits include retries.
Expired challenge/counter records are cleaned by the payment worker. Monitor that
worker: failed recovery email sends retry up to three times. `--skip-notifications`
also suppresses recovery email delivery.

The Nginx example includes branded browser error pages and JSON API responses
for proxy outages and request limiting. When updating an existing host, preserve
its real server name and certificate paths, validate with `nginx -t`, then reload.
The app deployment does not automatically replace the host's Nginx site file.

Run browser regressions from `frontend/stechpay` using `npm test` after installing
Chromium with `npx playwright install chromium`. They mock all API requests and
never send emails or initiate real payments. Backup uploader checks run with
`python deployment/test_backup_upload.py` from the repository root.

For rolling cloud backup retention, create an enabled Object Storage lifecycle
rule deleting objects with prefix `database/` after 30 days. This removes old
snapshots, not live database records. The regional Object Storage service needs
its own bucket-scoped lifecycle policy; keep the VM writer upload-only. Configure
the lifecycle service in the tenancy root using the actual region and bucket:

```text
Allow service objectstorage-<region> to manage object-family in tenancy where all {target.bucket.name = '<backup-bucket>', any {request.permission = 'BUCKET_INSPECT', request.permission = 'BUCKET_READ', request.permission = 'OBJECT_INSPECT', request.permission = 'OBJECT_DELETE'}}
```

Preserve existing lifecycle rules. Review storage usage, backup job failures and
restore readiness regularly. See [Oracle lifecycle permissions](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usinglifecyclepolicies.htm).

The development launchers include the payment worker. For manual startup, use a
separate process running `python manage.py process_payments --watch`. It calls the
configured Paystack API and sends queued emails. To reconcile without sending
emails, add `--skip-notifications`. `--skip-reconcile` processes only queued events
and notifications; it does not suppress emails. Do not start extra workers casually.

Inspect gateway events, notification retries and audit entries in the read-only
Django admin. Payment fields cannot be edited there. An additional successful
charge is retained as `duplicate`; a refund/dispute/reversal requires review and
does not issue a paid receipt. Perform any refund through the authorized Paystack
workflow, then allow its authenticated event to update the local record. There is
no automatic refund operation in this application.

For real-money activation, follow the [live payment rollout](LIVE_PAYMENTS.md).
It covers Direct Entry matric numbers, separate departmental live subaccounts,
technical preflight, policies, settlement checks and the controlled pilot.

Grant receipt reviewers `userapp.view_payment`. Grant bursary managers the needed
view/change/delete permissions for Fee, Department and SessionConfig as appropriate,
plus payment viewing. Merely setting `is_staff` no longer grants settlement-editing
access. Native Django sign-in redirects to the same MFA-protected application login.
