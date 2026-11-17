import json
import re
from io import StringIO
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .emails import send_payment_confirmation
from .models import Payment, PaymentObligation, ReceiptRecovery, LoginChallenge
from .paystack import PaystackError
from .recovery import deliver_recovery
from .throttles import consume_limits, SharedAPIThrottle


class ReceiptRecoveryTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.get('/api/csrf/')
        obligation = PaymentObligation.objects.create(matric_number='CSC/26/001', session_start=2026, mode='test')
        self.payment = Payment.objects.create(reference='STECH-TEST-RECOVERY', full_name='Recovery Student',
            email='student@example.invalid', matric_number='CSC/26/001', department_code='CSC', level=100,
            session_start=2026, amount=Decimal('25000'), status=Payment.Status.SUCCESS, allocation='applied', mode='test', obligation=obligation)

    def post(self, path, data=None, client=None):
        client = client or self.client
        return client.post(path, json.dumps(data or {}), content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)

    def start(self, reference=None):
        response = self.post('/api/receipts/recovery/', {'reference': reference or self.payment.reference, 'email': 'attacker@example.invalid'})
        self.assertEqual(response.status_code, 202)
        return ReceiptRecovery.objects.get(pk=self.client.session['receipt_recovery'])

    def deliver(self, challenge):
        deliver_recovery(challenge.pk)
        return re.search(r'\b[0-9]{6}\b', mail.outbox[-1].body).group()

    def verify(self, code):
        return self.post('/api/receipts/recovery/confirm/', {'code': code})

    def test_unknown_and_known_references_queue_identical_public_responses_without_smtp(self):
        with patch('userapp.recovery.send_mail') as sender:
            responses = [self.post('/api/receipts/recovery/', {'reference': ref}) for ref in [self.payment.reference, 'MISSING']]
        sender.assert_not_called()
        self.assertEqual([response.status_code for response in responses], [202, 202])
        first, second = [response.json() for response in responses]
        for key in ('detail', 'resends_remaining'):
            self.assertEqual(first[key], second[key])
        self.assertEqual(first.keys(), second.keys())
        self.assertNotIn(self.payment.email, json.dumps(first))
        self.assertEqual(ReceiptRecovery.objects.count(), 2)
        self.assertTrue(ReceiptRecovery.objects.filter(reference=self.payment.reference).get().used)

    def test_only_recorded_mailbox_receives_code_and_no_raw_code_is_stored(self):
        challenge = self.start()
        code = self.deliver(challenge)
        self.assertEqual(mail.outbox[-1].to, [self.payment.email])
        challenge.refresh_from_db()
        self.assertNotEqual(challenge.code_hash, code)
        self.assertEqual(challenge.code_hash, salted_hmac('receipt-code', code).hexdigest())
        deliver_recovery(challenge.pk)
        self.assertEqual(len(mail.outbox), 1)

    def test_verified_download_is_scoped_single_use_and_survives_refresh(self):
        challenge = self.start()
        code = self.deliver(challenge)
        url = f'/api/payments/{self.payment.reference}/receipt/'
        self.assertEqual(self.client.get(url).status_code, 404)
        stranger = Client(enforce_csrf_checks=True); stranger.get('/api/csrf/')
        self.assertEqual(self.post('/api/receipts/recovery/confirm/', {'code': code}, stranger).status_code, 400)
        response = self.verify(code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['receipt_id'], str(self.payment.receipt_id))
        self.assertEqual(self.verify(code).status_code, 400)
        self.assertEqual(self.client.get('/api/receipts/recovery/challenge/').json()['receipt_url'], url)
        self.assertFalse(self.client.get('/api/admin/me/').json()['authenticated'])
        self.assertEqual(self.post('/api/payments/verify/', {'reference': self.payment.reference}).status_code, 404)
        with patch('userapp.views.reconcile', return_value=self.payment) as reconcile:
            pdf = self.client.get(url)
        reconcile.assert_called_once()
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertEqual(stranger.get(url).status_code, 404)
        self.assertEqual(self.client.get('/api/payments/OTHER/receipt/').status_code, 404)
        self.assertEqual(Payment.objects.count(), 1)

    def test_grant_expiry_email_change_and_reversal_block_download(self):
        self.verify(self.deliver(self.start()))
        url = f'/api/payments/{self.payment.reference}/receipt/'
        with patch('userapp.views.reconcile', side_effect=PaystackError()):
            self.assertEqual(self.client.get(url).status_code, 503)
        self.payment.status = 'refunded'; self.payment.save()
        with patch('userapp.views.reconcile', return_value=self.payment):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.payment.status = Payment.Status.SUCCESS; self.payment.email = 'changed@example.invalid'; self.payment.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.payment.email = 'student@example.invalid'; self.payment.save()
        session = self.client.session
        session['receipt_grant'] = {**session['receipt_grant'], 'expires_at': int(timezone.now().timestamp()) - 1}
        session.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_resend_rotates_code_preserves_expiry_attempts_and_enforces_cooldown_cap(self):
        challenge = self.start()
        with patch('userapp.recovery.secrets.randbelow', return_value=123456):
            old = self.deliver(challenge)
        self.assertEqual(self.post('/api/receipts/recovery/challenge/').status_code, 429)
        self.assertEqual(self.verify('bad').status_code, 400)
        ReceiptRecovery.objects.filter(pk=challenge.pk).update(requested_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.post('/api/receipts/recovery/challenge/').status_code, 200)
        with patch('userapp.recovery.secrets.randbelow', return_value=654321):
            fresh = self.deliver(challenge)
        refreshed = ReceiptRecovery.objects.get(pk=challenge.pk)
        self.assertEqual(refreshed.expires_at, challenge.expires_at)
        self.assertEqual(refreshed.attempts, 1)
        self.assertEqual(self.verify(old).status_code, 400)
        ReceiptRecovery.objects.filter(pk=challenge.pk).update(resend_count=3, requested_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.post('/api/receipts/recovery/challenge/').status_code, 429)
        self.assertEqual(self.verify(fresh).status_code, 200)

    def test_expired_or_exhausted_challenges_cannot_verify_or_resend(self):
        challenge = self.start()
        code = self.deliver(challenge)
        for _ in range(5):
            self.assertEqual(self.verify('bad').status_code, 400)
        self.assertEqual(self.verify(code).json()['code'], 'challenge_expired')
        self.assertEqual(self.post('/api/receipts/recovery/challenge/').status_code, 400)
        ReceiptRecovery.objects.filter(pk=challenge.pk).update(attempts=0, expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(code).json()['code'], 'challenge_expired')

    def test_pending_duplicate_refunded_and_unknown_payments_do_not_send_codes(self):
        for status, allocation in [('pending', 'unallocated'), (Payment.Status.SUCCESS, 'duplicate'), ('refunded', 'review')]:
            self.payment.status, self.payment.allocation = status, allocation; self.payment.save()
            deliver_recovery(self.start().pk)
        deliver_recovery(self.start('MISSING').pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_delivery_failures_retry_without_exposing_error_and_resend_is_not_lost(self):
        challenge = self.start()
        with patch('userapp.recovery.send_mail', side_effect=RuntimeError('private SMTP details')):
            deliver_recovery(challenge.pk)
        challenge.refresh_from_db()
        self.assertTrue(challenge.delivery_requested)
        self.assertEqual(challenge.delivery_attempts, 1)
        self.assertNotIn('SMTP', self.client.get('/api/receipts/recovery/challenge/').content.decode())
        ReceiptRecovery.objects.filter(pk=challenge.pk).update(available_at=timezone.now())
        def during_send(*args, **kwargs):
            ReceiptRecovery.objects.filter(pk=challenge.pk).update(requested_at=timezone.now(), code_hash='', delivery_requested=True)
            return 1
        with patch('userapp.recovery.send_mail', side_effect=during_send):
            deliver_recovery(challenge.pk)
        challenge.refresh_from_db()
        self.assertTrue(challenge.delivery_requested)
        self.assertEqual(challenge.code_hash, '')

    def test_reference_budget_and_ip_budget_prevent_email_flooding(self):
        for _ in range(6):
            self.start()
        challenge = self.start()
        self.assertFalse(challenge.delivery_allowed)
        deliver_recovery(challenge.pk)
        self.assertEqual(len(mail.outbox), 0)
        for _ in range(3):
            self.start('MISSING')
        response = self.post('/api/receipts/recovery/', {'reference': 'MISSING'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)

    def test_csrf_required_and_email_has_recovery_link(self):
        self.assertEqual(self.client.post('/api/receipts/recovery/', {'reference': self.payment.reference}).status_code, 403)
        self.assertTrue(send_payment_confirmation(self.payment))
        self.assertIn('/receipts?ref=STECH-TEST-RECOVERY', mail.outbox[-1].body)
        self.assertIn('Retrieve your receipt', mail.outbox[-1].alternatives[0].content)

    def test_scheduled_worker_delivers_recovery_and_respects_skip_notifications(self):
        self.start()
        output = StringIO()
        call_command('process_payments', skip_reconcile=True, skip_notifications=True, stdout=output)
        self.assertEqual(len(mail.outbox), 0)
        call_command('process_payments', skip_reconcile=True, stdout=output)
        self.assertEqual(len(mail.outbox), 1)

    def test_mailbox_budget_applies_across_references(self):
        for _ in range(8):
            self.assertEqual(consume_limits('receipt-mailbox', [(self.payment.email, 8, 3600)]), 0)
        challenge = self.start()
        deliver_recovery(challenge.pk)
        self.assertEqual(len(mail.outbox), 0)
        challenge.refresh_from_db()
        self.assertFalse(challenge.delivery_requested)


class SharedLimitTests(TestCase):
    def test_new_throttle_instances_share_database_budget_and_window_resets(self):
        from types import SimpleNamespace
        from django.contrib.auth.models import AnonymousUser
        request = SimpleNamespace(user=AnonymousUser(), META={'REMOTE_ADDR': '192.0.2.10'})
        with override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES': {'anon': '2/minute'}, 'NUM_PROXIES': 0}):
            self.assertTrue(SharedAPIThrottle().allow_request(request, None))
            self.assertTrue(SharedAPIThrottle().allow_request(request, None))
            blocked = SharedAPIThrottle()
            self.assertFalse(blocked.allow_request(request, None))
            self.assertGreater(blocked.wait(), 0)
            with patch('userapp.throttles.timezone.now', return_value=timezone.now() + timedelta(seconds=61)):
                self.assertTrue(SharedAPIThrottle().allow_request(request, None))

    @override_settings(ADMIN_MFA_REQUIRED=True)
    def test_password_budget_does_not_consume_code_budget(self):
        User.objects.create_superuser('split-admin', 'admin@example.invalid', 'test-only-password')
        for _ in range(4):
            self.assertEqual(self.client.post('/api/admin/login/', {'username': 'split-admin', 'password': 'wrong'}).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/login/', {'username': 'split-admin', 'password': 'test-only-password'}).status_code, 200)
        code = re.search(r'\b[0-9]{6}\b', mail.outbox[-1].body).group()
        self.assertEqual(self.client.post('/api/admin/login/confirm/', {'code': code}).status_code, 200)
        self.assertTrue(LoginChallenge.objects.get().used)

    def test_counters_do_not_grow_on_denied_requests(self):
        from .models import LoginBucket
        self.assertEqual(consume_limits('test', [('identity', 1, 60)]), 0)
        for _ in range(5):
            self.assertGreater(consume_limits('test', [('identity', 1, 60)]), 0)
        self.assertEqual(LoginBucket.objects.get().count, 1)
