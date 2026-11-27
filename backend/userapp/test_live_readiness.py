"""Regression checks for matric admission routes and live settlement isolation."""
import json
import uuid
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from rest_framework.exceptions import ValidationError

from .matric import parse_matric
from .models import Department, Fee, GatewayEvent, Notification, Payment, SessionConfig
from .payments import process_event
from .paystack import PaystackError, verify_live_subaccount


class MatricAndSettlementTests(TestCase):
    def setUp(self):
        SessionConfig.objects.create(session_start=2026)
        self.department = Department.objects.create(code='STA', name='Statistics', subaccount_code='ACCT_test')
        Fee.objects.create(department='STA', level=200, amount='2000.00')
        Fee.objects.create(department='STA', level=300, amount='3000.00')
        self.client = Client(HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.client.get('/api/csrf/')
        self.data = {'full_name': 'Example Student', 'email': 'student@example.invalid', 'matric_number': 'STA/DE/26/001'}

    def test_direct_entry_starts_at_200_and_shares_regular_cohort_level(self):
        self.assertEqual(parse_matric('STA/DE/26/001')['level'], 200)
        regular, direct = parse_matric('STA/24/001'), parse_matric(' sta/de/25/002 ')
        self.assertEqual(regular['level'], 300)
        self.assertEqual(direct['level'], regular['level'])
        self.assertEqual(direct['normalized'], 'STA/DE/25/002')
        self.assertEqual(direct['entry_year'], 2025)
        self.assertEqual(direct['entry_level'], 200)
        SessionConfig.objects.update(session_start=2025)
        self.assertEqual(parse_matric('STA/DE/25/002')['level'], 200)
        self.assertEqual(parse_matric('STA/24/001')['level'], 200)

    def test_registration_numbers_and_invalid_de_formats_stay_rejected(self):
        for value in ('202612345678AB', 'STA/DE/27/001', 'STA/25/DE/001', 'STA/DE/2025/001', 'STA/DE/25/1', 'ABC/DE/25/001'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_matric(value)

    @patch('userapp.paystack.configured_mode', return_value='live')
    @patch('userapp.paystack.initialize_transaction')
    def test_live_requires_an_explicit_academic_session(self, initialize, mode):
        self.department.live_subaccount_code = 'ACCT_live'
        self.department.save()
        SessionConfig.objects.all().delete()
        self.assertEqual(self.initiate().status_code, 503)
        initialize.assert_not_called()
        self.assertFalse(Payment.objects.exists())

    @patch('userapp.paystack.configured_mode', return_value='live')
    @patch('userapp.paystack._request')
    def test_live_subaccount_preflight_rejects_wrong_mode_or_destination(self, request, mode):
        valid = {'subaccount_code': 'ACCT_live', 'domain': 'live', 'currency': 'NGN', 'active': True}
        for changes in ({'domain': 'test'}, {'active': False}, {'currency': 'USD'}, {'subaccount_code': 'ACCT_other'}):
            request.return_value = dict(valid, **changes)
            with self.subTest(changes=changes), self.assertRaises(PaystackError):
                verify_live_subaccount('ACCT_live')
        request.return_value = valid
        self.assertEqual(verify_live_subaccount('ACCT_live'), valid)
        request.assert_called_with('GET', '/subaccount/ACCT_live')

    def test_readiness_reports_blockers_without_contacting_paystack(self):
        from io import StringIO
        from django.core.management import call_command, CommandError
        output, errors = StringIO(), StringIO()
        with patch('userapp.paystack._request') as request, self.assertRaises(CommandError):
            call_command('check_payment_readiness', stdout=output, stderr=errors)
        request.assert_not_called()
        self.assertIn('live subaccount is missing', errors.getvalue())
        self.assertIn('Live credentials', errors.getvalue())

    @patch('userapp.paystack.initialize_transaction')
    def test_receipt_checker_rejects_test_receipts_in_live_mode_and_refreshes_status(self, initialize):
        from django.contrib.auth.models import User
        initialize.return_value = {'authorization_url': 'https://checkout.paystack.com/test'}
        self.assertEqual(self.initiate().status_code, 200)
        payment = Payment.objects.get()
        Payment.objects.filter(pk=payment.pk).update(status='successful', allocation='applied')
        self.client.force_login(User.objects.create_superuser(username='reviewer', email='reviewer@example.invalid'))
        url = '/api/admin/receipts/verify/'
        with override_settings(PAYSTACK_MODE='live'), patch('userapp.paystack.verify_transaction') as verify:
            response = self.client.get(url, {'code': payment.reference})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['valid'])
        verify.assert_not_called()
        raw = {'id': 123, 'reference': payment.reference, 'domain': 'test', 'status': 'reversed',
               'amount': 200000, 'currency': 'NGN', 'customer': {'email': payment.email},
               'subaccount': {'subaccount_code': 'ACCT_test'}}
        with patch('userapp.paystack.verify_transaction', return_value={'raw': raw}) as verify:
            response = self.client.get(url, {'code': payment.reference})
        verify.assert_called_once_with(payment.reference)
        self.assertFalse(response.json()['valid'])
        self.assertEqual(response.json()['payment']['status'], 'reversed')

    def initiate(self):
        return self.client.post('/api/payments/initiate/', json.dumps(self.data), content_type='application/json')

    @override_settings(PAYMENT_CHECKOUT_ENABLED=False)
    @patch('userapp.paystack.initialize_transaction')
    def test_checkout_pause_blocks_new_payments(self, initialize):
        self.assertEqual(self.initiate().status_code, 503)
        initialize.assert_not_called()
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(self.client.get('/api/meta/').json()['checkout_enabled'])

    @patch('userapp.paystack.initialize_transaction')
    def test_de_fee_is_selected_by_backend_and_test_route_is_preserved(self, initialize):
        initialize.return_value = {'authorization_url': 'https://checkout.paystack.com/test'}
        self.data.update(level=100, amount='1.00', direct_entry=False)
        self.assertEqual(self.initiate().status_code, 200)
        payment = Payment.objects.get()
        self.assertEqual(payment.level, 200)
        self.assertEqual(str(payment.amount), '2000.00')
        self.assertEqual(payment.subaccount_code, 'ACCT_test')
        self.assertEqual(initialize.call_args.kwargs['subaccount'], 'ACCT_test')

    @patch('userapp.paystack.configured_mode', return_value='live')
    @patch('userapp.paystack.initialize_transaction')
    def test_live_never_falls_back_to_test_or_main_account(self, initialize, mode):
        response = self.initiate()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Payment.objects.exists())
        initialize.assert_not_called()
        self.department.live_subaccount_code = 'ACCT_live'
        self.department.save()
        initialize.return_value = {'authorization_url': 'https://checkout.paystack.com/live'}
        self.assertEqual(self.initiate().status_code, 200)
        payment = Payment.objects.get()
        self.assertEqual(payment.mode, 'live')
        self.assertEqual(payment.subaccount_code, 'ACCT_live')
        self.assertEqual(initialize.call_args.kwargs['subaccount'], 'ACCT_live')

    @patch('userapp.paystack.initialize_transaction')
    def test_webhook_waits_for_authoritative_verification_and_retries(self, initialize):
        initialize.return_value = {'authorization_url': 'https://checkout.paystack.com/test'}
        self.assertEqual(self.initiate().status_code, 200)
        payment = Payment.objects.get()
        raw = {'id': 123, 'reference': payment.reference, 'domain': 'test', 'status': 'success',
               'amount': 200000, 'currency': 'NGN', 'customer': {'email': payment.email},
               'subaccount': {'subaccount_code': 'ACCT_test'}}
        event = GatewayEvent.objects.create(digest='a'*64, event_type='charge.success',
                                           payload={'event': 'charge.success', 'data': raw})
        with patch('userapp.paystack.verify_transaction', side_effect=PaystackError()), self.assertRaises(PaystackError):
            process_event(event.pk)
        event.refresh_from_db()
        payment.refresh_from_db()
        self.assertIsNone(event.processed_at)
        self.assertEqual(payment.status, 'pending')
        self.assertFalse(Notification.objects.exists())
        # A signed success does not override a current reversed transaction.
        with override_settings(PAYMENT_CHECKOUT_ENABLED=False), patch('userapp.paystack.verify_transaction', return_value={'raw': dict(raw, status='reversed')}):
            process_event(event.pk)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'reversed')
        self.assertFalse(payment.receipt_available)
        self.assertFalse(Notification.objects.exists())
