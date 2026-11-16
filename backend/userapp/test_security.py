import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth.models import User, Permission
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import ValidationError

from . import paystack
from .admin import PaymentAdmin
from .emails import send_payment_confirmation
from .management.commands.process_payments import deliver_notification
from .models import CheckoutRequest, Department, Fee, GatewayEvent, LoginChallenge, Notification, Payment, PaymentObligation, SessionConfig
from .payments import apply_verification, process_event
from .security import owner_hash


class SecurityTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.get('/api/csrf/')
        SessionConfig.objects.create(session_start=2026)
        Department.objects.create(code='CSC', name='Computer Science')
        Fee.objects.create(department='CSC', level=100, amount=Decimal('25000'))
        self.data = {'full_name': 'Audit Student', 'email': 'student@example.invalid', 'matric_number': 'CSC/26/001'}
        self.key = str(uuid.uuid4())
        self.initializer = patch('userapp.paystack.initialize_transaction', side_effect=lambda **kw: {
            'reference': kw['reference'], 'authorization_url': 'https://checkout.paystack.com/' + kw['reference']})
        self.init = self.initializer.start()
        self.addCleanup(self.initializer.stop)

    def post(self, path, data=None, client=None, csrf=True, **headers):
        client = client or self.client
        if csrf:
            headers['HTTP_X_CSRFTOKEN'] = client.cookies['csrftoken'].value
        headers.setdefault('HTTP_IDEMPOTENCY_KEY', self.key)
        return client.post(path, json.dumps(data or {}), content_type='application/json', **headers)

    def checkout(self):
        response = self.post('/api/payments/initiate/', self.data)
        self.assertEqual(response.status_code, 200, response.content)
        return Payment.objects.get(reference=response.json()['reference'])

    def raw(self, payment, **overrides):
        result = {'id': payment.pk + 10000, 'reference': payment.reference, 'domain': payment.mode,
                  'status': 'success', 'amount': 2500000, 'currency': 'NGN',
                  'customer': {'email': payment.email}, 'paid_at': timezone.now().isoformat(), 'channel': 'card'}
        result.update(overrides)
        return result

    def webhook(self, data, event='charge.success', valid=True):
        body = json.dumps({'event': event, 'data': data})
        signature = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body.encode(), hashlib.sha512).hexdigest()
        return self.client.post('/api/payments/webhook/', body, content_type='application/json',
                                HTTP_X_PAYSTACK_SIGNATURE=signature if valid else 'invalid')

    def test_missing_and_mock_credentials_never_approve(self):
        for changes in [{'PAYSTACK_SECRET_KEY': ''}, {'PAYSTACK_MOCK_MODE': True}, {'PAYSTACK_SECRET_KEY': 'mock'}]:
            with self.subTest(changes=changes), override_settings(**changes):
                response = self.post('/api/payments/initiate/', self.data)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.webhook({'reference': 'fake'}).status_code, 401)
        self.assertFalse(Payment.objects.exists())

    def test_real_test_mode_allowed_live_requires_opt_in(self):
        self.assertEqual(paystack.configured_mode(), 'test')
        with override_settings(PAYSTACK_MODE='live', PAYSTACK_SECRET_KEY='sk_live_' + 'b' * 40):
            with self.assertRaises(paystack.PaystackError):
                paystack.configured_mode()

    def test_idempotency_replays_and_rejects_changed_payload(self):
        payment = self.checkout()
        again = self.post('/api/payments/initiate/', self.data)
        self.assertEqual(again.json()['reference'], payment.reference)
        changed = self.post('/api/payments/initiate/', dict(self.data, full_name='Different Name'))
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.init.call_count, 1)

    def test_alternate_retry_keys_are_persisted(self):
        payment = self.checkout()
        self.key = str(uuid.uuid4())
        self.assertEqual(self.post('/api/payments/initiate/', self.data).json()['reference'], payment.reference)
        self.assertEqual(CheckoutRequest.objects.count(), 2)
        self.assertEqual(self.post('/api/payments/initiate/', dict(self.data, full_name='Changed Name')).status_code, 409)

    def test_interleaved_checkout_never_initializes_twice(self):
        calls = []
        def initialize(**kw):
            calls.append(kw['reference'])
            if len(calls) == 1:
                inner = self.post('/api/payments/initiate/', self.data, HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
                self.assertEqual(inner.status_code, 202)
            return {'authorization_url': 'https://checkout.paystack.com/test', 'reference': kw['reference']}
        self.init.side_effect = initialize
        self.checkout()
        self.assertEqual(len(calls), 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_pending_attempt_is_not_superseded_after_fifteen_minutes(self):
        payment = self.checkout()
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.key = str(uuid.uuid4())
        self.assertEqual(self.post('/api/payments/initiate/', self.data).json()['reference'], payment.reference)
        self.assertEqual(self.init.call_count, 1)

    def test_unknown_initialization_outcome_remains_pending(self):
        self.init.side_effect = paystack.PaystackError()
        self.assertEqual(self.post('/api/payments/initiate/', self.data).status_code, 502)
        self.assertEqual(Payment.objects.get().status, 'pending')
        self.key = str(uuid.uuid4())
        self.assertEqual(self.post('/api/payments/initiate/', self.data).status_code, 202)
        self.assertEqual(self.init.call_count, 1)

    def test_initialization_recovery_reuses_reserved_reference(self):
        from .payments import recover_initialization
        self.init.side_effect = paystack.PaystackError()
        self.post('/api/payments/initiate/', self.data)
        payment = Payment.objects.get()
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(minutes=2))
        payment.refresh_from_db()
        self.init.side_effect = lambda **kw: {'reference': kw['reference'], 'authorization_url': 'https://checkout.paystack.com/recovered'}
        recover_initialization(payment)
        self.assertEqual(self.init.call_args.kwargs['reference'], payment.reference)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(self.post('/api/payments/initiate/', self.data).json()['reference'], payment.reference)

    def test_database_prevents_two_allocated_payments(self):
        first = self.checkout()
        apply_verification(first, self.raw(first))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(**self.data, reference='OTHER-CHARGE', amount=first.amount, department_code='CSC',
                level=100, session_start=2026, obligation=first.obligation, status='successful', allocation='applied')

    def test_test_obligation_does_not_satisfy_live_dues(self):
        first = self.checkout()
        apply_verification(first, self.raw(first))
        self.key = str(uuid.uuid4())
        with patch('userapp.paystack.configured_mode', return_value='live'):
            second = self.checkout()
        self.assertEqual(second.mode, 'live')
        self.assertNotEqual(first.obligation_id, second.obligation_id)

    def test_deployment_check_rejects_sqlite_and_allows_secure_test_staging(self):
        from .checks import deployment_checks
        self.assertIn('userapp.E003', [issue.id for issue in deployment_checks(None)])
        with override_settings(APP_ENV='staging', DEBUG=False, ADMIN_MFA_REQUIRED=True, SECURE_SSL_REDIRECT=True,
            SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, SECURE_HSTS_SECONDS=31536000,
            SECRET_KEY='security-check-only-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJ',
            FRONTEND_URL='https://payments.example.invalid', EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend'):
            with patch.dict(settings.DATABASES['default'], ENGINE='django.db.backends.postgresql'):
                self.assertEqual(deployment_checks(None), [])

    def test_other_browser_cannot_get_details_reference_or_receipt(self):
        payment = self.checkout()
        apply_verification(payment, self.raw(payment))
        other = Client(enforce_csrf_checks=True)
        other.get('/api/csrf/')
        response = self.post('/api/payments/initiate/', self.data, client=other, HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('payment', response.json())
        self.assertEqual(self.post('/api/payments/verify/', {'reference': payment.reference}, client=other).status_code, 404)
        self.assertEqual(other.get(f'/api/payments/{payment.reference}/receipt/?code={payment.receipt_id}').status_code, 404)
        receipt = self.client.get(f'/api/payments/{payment.reference}/receipt/')
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt['Cache-Control'], 'private, no-store')

    def test_strict_gateway_matching(self):
        payment = self.checkout()
        cases = [{'amount': None}, {'currency': None}, {'reference': 'wrong'}, {'id': None},
                 {'amount': '2500000'}, {'amount': True}, {'amount': 1}, {'currency': 'USD'},
                 {'domain': 'live'}, {'customer': {'email': 'wrong@example.invalid'}}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                apply_verification(payment, self.raw(payment, **changes))
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'pending')
        self.assertFalse(Notification.objects.exists())

    def test_settlement_destination_must_match(self):
        payment = self.checkout()
        payment.subaccount_code = 'ACCT_expected'
        payment.save()
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            apply_verification(payment, self.raw(payment))
        self.assertTrue(apply_verification(payment, self.raw(payment, subaccount={'subaccount_code': 'ACCT_expected'})).receipt_available)

    def test_invalid_provider_timestamp_does_not_crash_verified_payment(self):
        payment = self.checkout()
        updated = apply_verification(payment, self.raw(payment, paid_at='2026-99-99T10:00:00Z'))
        self.assertTrue(updated.receipt_available)
        self.assertIsNotNone(updated.paid_at)

    def test_stale_failure_does_not_overwrite_success(self):
        payment = self.checkout()
        def verify(reference):
            apply_verification(payment, self.raw(payment))
            return {'status': 'failed', 'raw': self.raw(payment, status='failed')}
        with patch('userapp.paystack.verify_transaction', side_effect=verify):
            response = self.post('/api/payments/verify/', {'reference': payment.reference})
        self.assertEqual(response.json()['payment']['status'], 'successful')
        payment.refresh_from_db()
        self.assertIsNotNone(payment.paid_at)

    def test_late_old_success_is_preserved_as_duplicate(self):
        old = self.checkout()
        apply_verification(old, self.raw(old, status='failed'))
        self.key = str(uuid.uuid4())
        new = self.checkout()
        apply_verification(new, self.raw(new))
        duplicate = apply_verification(old, self.raw(old))
        self.assertEqual(duplicate.status, 'successful')
        self.assertEqual(duplicate.allocation, 'duplicate')
        self.assertFalse(duplicate.receipt_available)
        self.assertEqual(Payment.objects.filter(allocation='applied').count(), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_duplicate_event_persisted_once_and_notification_once(self):
        payment = self.checkout()
        for _ in range(2):
            self.assertEqual(self.webhook(self.raw(payment, paid_at='2026-11-16T00:00:00Z')).status_code, 200)
        self.assertEqual(GatewayEvent.objects.count(), 1)
        self.assertFalse(mail.outbox)
        event = GatewayEvent.objects.get()
        process_event(event.pk)
        process_event(event.pk)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertFalse(mail.outbox)
        deliver_notification(Notification.objects.get().pk)
        deliver_notification(Notification.objects.get().pk)
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_signature_or_missing_success_fields_not_applied(self):
        payment = self.checkout()
        self.assertEqual(self.webhook(self.raw(payment), valid=False).status_code, 401)
        self.assertFalse(GatewayEvent.objects.exists())
        self.webhook({'reference': payment.reference, 'status': 'success'})
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            process_event(GatewayEvent.objects.get().pk)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'pending')

    def test_modern_webhook_signature_rejects_replay_and_downgrade(self):
        import time
        body = b'{}'
        secret = 'whsec_audit_only'
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha512).hexdigest()
        with override_settings(PAYSTACK_WEBHOOK_SECRETS=[secret]):
            self.assertTrue(paystack.valid_webhook(body, {'Paystack-Signature': f't={stamp},v1=whk_test:{signature}'}))
            self.assertFalse(paystack.valid_webhook(body, {'Paystack-Signature': f't=1,v1=whk_test:{signature}'}))
            self.assertFalse(paystack.valid_webhook(body, {'x-paystack-signature': signature}))

    def test_refund_invalidates_receipt_and_success_cannot_restore_it(self):
        payment = self.checkout()
        payment = apply_verification(payment, self.raw(payment))
        self.webhook({'id': 444, 'transaction': {'id': int(payment.provider_id), 'reference': payment.reference}}, 'refund.processed')
        with patch('userapp.paystack.verify_transaction', return_value={'status': 'success', 'raw': self.raw(payment)}):
            process_event(GatewayEvent.objects.get().pk)
        payment = apply_verification(payment, self.raw(payment))
        self.assertEqual(payment.status, 'refunded')
        self.assertFalse(payment.receipt_available)

    def test_rollback_does_not_enqueue_or_send_confirmation(self):
        payment = self.checkout()
        try:
            with transaction.atomic():
                apply_verification(payment, self.raw(payment))
                raise RuntimeError('rollback')
        except RuntimeError:
            pass
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(mail.outbox)

    def test_email_failure_is_retried_and_html_is_escaped(self):
        self.data['full_name'] = '<a href="https://example.invalid">Link</a>'
        payment = self.checkout()
        payment = apply_verification(payment, self.raw(payment))
        notice = Notification.objects.get()
        with patch('userapp.management.commands.process_payments.send_payment_confirmation', return_value=False):
            deliver_notification(notice.pk)
        notice.refresh_from_db()
        self.assertIsNone(notice.sent_at)
        self.assertGreater(notice.available_at, timezone.now())
        self.assertTrue(send_payment_confirmation(payment))
        self.assertNotIn('<a href=', mail.outbox[-1].alternatives[0].content)
        self.assertIn('&lt;a href=', mail.outbox[-1].alternatives[0].content)

    def test_login_and_guest_checkout_require_csrf(self):
        user = User.objects.create_superuser('admin', 'admin@example.invalid', 'audit-password')
        response = self.client.post('/api/admin/login/', {'username': user.username, 'password': 'audit-password'}, HTTP_ORIGIN='https://untrusted.invalid')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post('/api/payments/initiate/', self.data, csrf=False).status_code, 403)
        self.assertEqual(self.client.get('/admin/login/').status_code, 302)

    def test_exposed_password_is_blocked_even_if_it_matches_user(self):
        User.objects.create_superuser('admin', 'admin@example.invalid', 'audit-password')
        with patch('userapp.passwords.EXPOSED_PASSWORD_SHA256', hashlib.sha256(b'audit-password').hexdigest()):
            response = self.post('/api/admin/login/', {'username': 'admin', 'password': 'audit-password'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.client.get('/api/admin/me/').json()['authenticated'])

    def test_forwarded_headers_do_not_bypass_account_login_limit(self):
        for number in range(5):
            response = self.post('/api/admin/login/', {'username': 'missing', 'password': 'incorrect'}, HTTP_X_FORWARDED_FOR=f'{number}, 203.0.113.1')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post('/api/admin/login/', {'username': 'missing', 'password': 'incorrect'}, HTTP_X_FORWARDED_FOR='new, 203.0.113.1').status_code, 429)

    @override_settings(ADMIN_MFA_REQUIRED=True)
    def test_mfa_required_and_code_single_use(self):
        User.objects.create_superuser('admin', 'admin@example.invalid', 'audit-password')
        response = self.post('/api/admin/login/', {'username': 'admin', 'password': 'audit-password'})
        self.assertTrue(response.json()['mfa_required'])
        self.assertFalse(self.client.get('/api/admin/me/').json()['authenticated'])
        import re
        code = re.search(r'\b[0-9]{6}\b', mail.outbox[-1].body).group()
        self.assertEqual(self.post('/api/admin/login/confirm/', {'username': 'admin', 'code': code}).status_code, 200)
        self.assertTrue(LoginChallenge.objects.get().used)
        self.assertEqual(self.post('/api/admin/login/confirm/', {'username': 'admin', 'code': code}).status_code, 400)
        self.assertTrue(self.client.get('/api/admin/me/').json()['authenticated'])

    def test_staff_without_model_permissions_cannot_route_funds(self):
        user = User.objects.create_user('reader', password='audit-password', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_payment'))
        self.client.force_login(user)
        self.assertEqual(self.client.get('/api/admin/payments/').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/departments/').status_code, 403)
        self.assertEqual(self.post('/api/admin/departments/', {'code': 'BAD', 'name': 'Bad Department'}).status_code, 403)

    def test_formula_export_neutralized(self):
        self.data['full_name'] = '=1+1'
        self.checkout()
        self.client.force_login(User.objects.create_superuser('admin', 'admin@example.invalid', 'audit-password'))
        response = self.client.get('/api/admin/payments/export/')
        self.assertIn("'=1+1", response.content.decode())

    def test_length_validation_and_json_encoding(self):
        import bz2
        self.assertEqual(self.post('/api/payments/initiate/', dict(self.data, full_name='A' * 121)).status_code, 400)
        response = self.client.generic('POST', '/api/payments/initiate/', bz2.compress(json.dumps(self.data).encode()),
            content_type='application/json; charset=bz2_codec', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.exists())

    def test_provider_adapter_rejects_wrong_ref_and_untrusted_checkout_url(self):
        self.initializer.stop()
        response = Mock(status_code=200)
        response.json.return_value = {'status': True, 'data': {'reference': 'wrong'}}
        with patch('userapp.paystack.requests.request', return_value=response):
            with self.assertRaises(paystack.PaystackError):
                paystack.verify_transaction('expected')
            response.json.return_value = {'status': True, 'data': {'reference': 'expected', 'authorization_url': 'https://evil.invalid/pay'}}
            with self.assertRaises(paystack.PaystackError):
                paystack.initialize_transaction('a@example.invalid', Decimal('10'), 'expected', 'http://localhost/callback')

    def test_verification_outage_does_not_mark_payment_failed(self):
        payment = self.checkout()
        with patch('userapp.paystack.verify_transaction', side_effect=paystack.PaystackError()):
            self.assertEqual(self.post('/api/payments/verify/', {'reference': payment.reference}).status_code, 503)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'pending')

    def test_success_is_reverified_and_reversal_removes_receipt(self):
        payment = self.checkout()
        apply_verification(payment, self.raw(payment))
        with patch('userapp.paystack.verify_transaction', return_value={'status': 'reversed', 'raw': self.raw(payment, status='reversed')}) as verify:
            result = self.post('/api/payments/verify/', {'reference': payment.reference})
        verify.assert_called_once()
        self.assertEqual(result.json()['payment']['status'], 'reversed')
        self.assertIsNone(result.json()['payment']['receipt_url'])
