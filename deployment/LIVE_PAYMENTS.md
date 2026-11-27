# Live payment rollout

Status: readiness preparation. Do not enable public live collection until the
technical checks, institutional decisions and controlled live pilot below pass.
Privacy, terms and refund pages are deliberately deferred pending confirmation
of the collecting organisation, support contact and approved rules.

## Student identification

Continue to require an assigned matric number. Regular numbers use
`STA/24/001`; Direct Entry numbers use `STA/DE/25/002`. The year is the actual
year of admission. Regular admission starts at 100 level; DE starts at 200.
The selected academic session determines subsequent progression. In 2026/27,
both examples are 300 level. Existing 100–500 level limits remain unchanged.

This validates format and calculates a fee; it does not prove the matric number
belongs to that student or check an admissions register. JAMB numbers, repeated
years, interrupted studies and exceptional progression still need a separately
approved approach. Do not tell students to invent matric numbers or remove DE.

## Departmental settlement and fees

1. Obtain the institution's approval to collect these dues and confirmation of
   the Paystack merchant responsible for the funds. Confirm live activation in
   Paystack; having credentials alone is not proof of institutional approval.
2. Create a live subaccount for each collecting department in the correct
   Paystack business. Independently check the bank, beneficiary and account
   number against the department's authorised banking details.
3. Confirm each subaccount's split percentage. Paystack's `percentage_charge`
   is the main account's share. A subaccount code alone does not mean all funds
   reach that department. Do not assume a percentage without approval.
4. The current integration sends `bearer="subaccount"`: Paystack processing
   fees are borne by the subaccount. Confirm this arrangement with the institution
   and disclose any separately approved student charges before payment.
5. In Administration → Departments, fill **Live subaccount**. The existing
   **Test subaccount** remains separate. Migration 0009 leaves live codes empty;
   it never copies a test code into a live field. Missing live destinations
   block checkout instead of falling back to the main merchant account.
6. Review every intended department/level fee and pin the actual academic
   session using Administration → Active Session → Set Session. New live
   checkouts require a manual session. No automatic calendar rollover is allowed
   for live collection. Missing fees already prevent checkout.
7. Fees are currently shared configuration, not separate test/live or session
   price lists. Do not change genuine student dues merely to run a small pilot.
   Agree a legitimate pilot obligation and its accounting treatment first.

References: [Paystack split payments](https://paystack.com/docs/payments/split-payments/)
and [subaccount fields](https://paystack.com/docs/api/subaccount/).

## Technical preflight

Run after migrations, using the deployed virtualenv and private environment.
The following management commands neither create payments nor send emails:

```bash
python manage.py check --deploy --fail-level WARNING
python manage.py check_payment_readiness
python manage.py check_payment_readiness --verify-subaccounts
```

The last command makes read-only Paystack requests with the configured live key
to check each live subaccount's identity, mode, currency and active state. It
does not certify ownership of the bank account or the approved split percentage.
In test mode the readiness command deliberately exits unsuccessfully; this does
not mean test payments are broken. Never put real credentials in source control,
CI output, screenshots or chat. Use a private candidate environment to preflight
live credentials before restarting the publicly served application with them.

Verify all of the following before a live cutover:

- CI backend tests on PostgreSQL and SQLite, browser tests, build and audits pass.
- PostgreSQL is local/private, uses a restricted application role and has a fresh
  off-server backup. Confirm the upload receipt and rehearse an isolated restore.
  The 30-day object lifecycle deletes snapshots, not production payment records.
- HTTPS validates in a normal browser; certificate auto-renewal is healthy.
- MFA and secure cookies are enabled; only approved staff can edit destinations,
  sessions and fees. Protect the Paystack and cloud accounts with MFA too.
- Live webhook URL is `https://<public-host>/api/payments/webhook/` and callback
  URL is `https://<public-host>/callback`. Configure the **live** Paystack settings,
  not only the test dashboard. Keep legacy HMAC versus dedicated signing-secret
  configuration consistent with the actual endpoint; do not mix modes' secrets.
- The payment worker runs, event backlog stays small, confirmation/recovery
  emails arrive, and someone receives actionable outage and failed-job alerts.
  A successful timer exit alone is insufficient: inspect worker retry counts.
- Keep Node/build tooling, OS, Python dependencies and database patches supported.
- Reconcile all pending test events before changing mode. Preserve test records
  for audit; they cannot satisfy live obligations or prove a live payment.

Success webhooks now cause a fresh verification API call. Verification checks
reference, provider ID, amount in kobo, currency, mode, customer and expected
subaccount before applying one payment to an obligation. API failure leaves the
webhook queued for retry. Staff receipt verification also checks current gateway
status and refuses receipts from another mode.

## Operational and policy decisions

Before public collection, confirm and publish the collecting organisation,
support channel, purpose and amount of the dues, approved refund procedure,
privacy notice and applicable terms. Establish who handles incorrect payments,
additional charges, disputes and complaints, including an absence cover person.
Do not promise automatic refunds: the application currently flags financial
events for review; authorised staff initiate refunds through Paystack.

The privacy review needs the actual controller, contact details, processing
purposes and lawful basis, recipients/processors, retention periods, rights and
complaint route. Include the real hosting/backup locations and email/payment
providers when assessing transfers. Do not use the backup retention period as
the retention period for live financial records. Confirm any applicable NDPC
registration/compliance obligations with the institution's privacy adviser.
See [Nigeria Data Protection Act, section 27](https://ndpc.gov.ng/wp-content/uploads/2024/03/Nigeria_Data_Protection_Act_2023.pdf).

HTTPS encrypts connections; it is not financial insurance. This review does not
establish insurance cover, institutional authorisation or legal compliance.
If insurance is intended, confirm the required cover with the institution and
its adviser. Paystack's security certifications do not certify this entire app.
Card entry stays on Paystack hosted checkout; never add card-number/CVV storage
or collection to the application. See [Paystack security](https://paystack.com/security).

## Controlled activation and pilot

1. Complete the decisions above and deploy tested code through CI/CD while still
   in test mode. Confirm migration 0009 and all live subaccount/fee/session data.
2. Privately configure the matching live secret, live webhook signing settings
   if used, `APP_ENV=production`, `PAYSTACK_MODE=live`, `ALLOW_LIVE_PAYMENTS=True`,
   with `DEBUG=False` and PostgreSQL. Run both preflight commands before exposing
   live checkout. The hosted checkout flow does not use a browser public key.
3. Use an agreed restricted pilot window. A human payer knowingly completes the
   approved real payment; do not automate a debit or reuse test credentials.
4. Verify exact amount, reference, applied allocation and live mode in the app
   and Paystack. Refresh/retry without creating a second charge. Test a closed
   callback tab, confirmation email, receipt recovery and staff verification.
5. Confirm actual settlement into the intended department bank account and the
   agreed split/fees. Transaction success alone does not prove bank settlement.
6. Exercise an approved refund/reversal process and confirm the receipt is no
   longer accepted. Keep evidence of the pilot and resolve discrepancies before
   announcing general availability. See [Paystack refunds](https://paystack.com/docs/payments/refunds/)
   and [disputes](https://paystack.com/docs/payments/manage-disputes/).
7. If problems occur, disable **new** live checkouts by setting
   `PAYMENT_CHECKOUT_ENABLED=False` in the app service environment and restarting
   the app. This leaves verification, webhooks, receipts and the payment worker
   available for existing transactions. Keep `ALLOW_LIVE_PAYMENTS=True`, live
   mode and live credentials during this pause so outstanding payments continue
   to reconcile. Confirm the pause response and worker processing after restart.
   Never switch the entire service back to test mode while live payments remain
   pending. Do not restore a pre-payment database over newly collected money.

Technical checks passing is one part of launch readiness. Do not represent this
checklist or an automated scan as a guarantee that the system has no vulnerabilities.
