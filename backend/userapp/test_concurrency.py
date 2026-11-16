"""Run against PostgreSQL in CI, or a dedicated file-backed SQLite test DB."""
import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.db import connection, connections
from django.test import Client, TransactionTestCase

from .models import Department, Fee, Notification, Payment, PaymentObligation, SessionConfig
from .payments import apply_verification


class ConcurrentPaymentTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor == 'sqlite' and not getattr(settings, 'TEST_SQLITE_FILE', ''):
            self.skipTest('Concurrency requires PostgreSQL or TEST_SQLITE_FILE (not shared in-memory SQLite).')
        SessionConfig.objects.create(session_start=2026)
        Department.objects.create(code='CSC', name='Computer Science')
        Fee.objects.create(department='CSC', level=100, amount=Decimal('25000'))
        self.data = {'full_name': 'Concurrent Student', 'email': 'concurrent@example.invalid', 'matric_number': 'CSC/26/001'}
        client = Client()
        client.get('/api/csrf/')
        self.cookies = copy.deepcopy(client.cookies)

    def submit(self, key, data=None):
        try:
            client = Client()
            client.cookies = copy.deepcopy(self.cookies)
            return client.post('/api/payments/initiate/', json.dumps(data or self.data), content_type='application/json', HTTP_IDEMPOTENCY_KEY=key)
        finally:
            connections.close_all()

    def test_simultaneous_initializations_create_one_gateway_checkout(self):
        started, release = threading.Event(), threading.Event()
        barrier = threading.Barrier(4)
        def initialize(**kw):
            started.set()
            if not release.wait(10):
                raise RuntimeError('Concurrent retry did not complete while the gateway was waiting.')
            return {'authorization_url': 'https://checkout.paystack.com/test', 'reference': kw['reference']}
        def worker():
            barrier.wait(timeout=10)
            return self.submit(str(uuid.uuid4()))
        with patch('userapp.paystack.initialize_transaction', side_effect=initialize) as gateway, ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker) for _ in range(4)]
            try:
                self.assertTrue(started.wait(10))
                done, _ = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
                self.assertTrue(done)
                self.assertEqual(next(iter(done)).result().status_code, 202)
            finally:
                release.set()
            responses = [future.result(timeout=20) for future in futures]
            self.assertTrue(all(response.status_code in (200, 202) for response in responses))
            self.assertEqual(gateway.call_count, 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_two_successful_attempts_allocate_dues_once(self):
        obligation = PaymentObligation.objects.create(matric_number=self.data['matric_number'], session_start=2026, mode='test')
        payments = [Payment.objects.create(**self.data, reference=f'CONCURRENT-{i}', department_code='CSC',
                    level=100, session_start=2026, amount=Decimal('25000'), obligation=obligation) for i in range(2)]
        barrier = threading.Barrier(2)
        def fulfill(pk):
            try:
                barrier.wait(timeout=10)
                payment = Payment.objects.get(pk=pk)
                raw = {'id': pk + 10000, 'reference': payment.reference, 'domain': 'test', 'status': 'success',
                       'amount': 2500000, 'currency': 'NGN', 'customer': {'email': payment.email}}
                return apply_verification(payment, raw).allocation
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(fulfill, [p.pk for p in payments]))
        self.assertCountEqual(outcomes, ['applied', 'duplicate'])
        self.assertEqual(Notification.objects.count(), 1)

    def test_same_idempotency_key_cannot_create_different_obligations(self):
        key = str(uuid.uuid4())
        barrier = threading.Barrier(2)
        def worker(number):
            barrier.wait(timeout=10)
            return self.submit(key, dict(self.data, matric_number=f'CSC/26/00{number}')).status_code
        with patch('userapp.paystack.initialize_transaction', side_effect=lambda **kw: {
            'reference': kw['reference'], 'authorization_url': 'https://checkout.paystack.com/test'}), ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(worker, [1, 2]))
        self.assertCountEqual(statuses, [200, 409])
        self.assertEqual(Payment.objects.count(), 1)
