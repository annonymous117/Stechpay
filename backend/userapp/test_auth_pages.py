import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from stechpay import errors
from .models import LoginBucket, LoginChallenge


@override_settings(ADMIN_MFA_REQUIRED=True)
class AuthPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('otp-admin', 'admin@example.invalid', 'test-only-password')
        self.client = Client(enforce_csrf_checks=True)
        self.client.get('/api/csrf/')

    def post(self, path, data=None):
        return self.client.post(path, json.dumps(data or {}), content_type='application/json',
                                HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def start(self):
        self.assertEqual(self.post('/api/admin/login/', {'username': self.user.username, 'password': 'test-only-password'}).status_code, 200)
        return LoginChallenge.objects.get(used=False)

    def test_refresh_session_binding_and_no_email_on_refresh(self):
        challenge = self.start()
        self.assertEqual(self.client.get('/api/admin/login/challenge/').json()['expires_at'], challenge.expires_at.isoformat())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Client().get('/api/admin/login/challenge/').status_code, 400)
        self.assertFalse(self.client.get('/api/admin/me/').json()['authenticated'])

    def test_resend_cooldown_rotates_code_without_extending_expiry_or_resetting_attempts(self):
        with patch('userapp.views.secrets.randbelow', return_value=123456):
            challenge = self.start()
        self.assertEqual(self.post('/api/admin/login/challenge/').status_code, 429)
        LoginChallenge.objects.filter(pk=challenge.pk).update(sent_at=timezone.now() - timedelta(seconds=61), attempts=1)
        with patch('userapp.views.secrets.randbelow', return_value=654321):
            self.assertEqual(self.post('/api/admin/login/challenge/').status_code, 200)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 1)
        self.assertEqual(challenge.resend_count, 1)
        self.assertEqual(self.post('/api/admin/login/confirm/', {'code': '123456'}).status_code, 400)
        self.assertEqual(self.post('/api/admin/login/confirm/', {'code': '654321'}).status_code, 200)

    def test_expired_exhausted_and_password_reset_challenges_cannot_resend(self):
        challenge = self.start()
        LoginChallenge.objects.filter(pk=challenge.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post('/api/admin/login/challenge/').json()['code'], 'challenge_expired')
        LoginChallenge.objects.filter(pk=challenge.pk).update(expires_at=timezone.now() + timedelta(minutes=5), attempts=5)
        self.assertEqual(self.client.get('/api/admin/login/challenge/').status_code, 400)
        LoginChallenge.objects.filter(pk=challenge.pk).update(attempts=0)
        self.user.set_password('another-test-password'); self.user.save()
        self.assertEqual(self.client.get('/api/admin/login/challenge/').status_code, 400)
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_delivery_keeps_previous_code_and_resend_limit(self):
        challenge = self.start()
        code = re.search(r'\b[0-9]{6}\b', mail.outbox[-1].body).group()
        LoginChallenge.objects.filter(pk=challenge.pk).update(sent_at=timezone.now() - timedelta(seconds=61))
        with patch('userapp.views.send_mail', side_effect=RuntimeError('private diagnostic')):
            response = self.post('/api/admin/login/challenge/')
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'private diagnostic', status_code=503)
        challenge.refresh_from_db()
        self.assertEqual(challenge.resend_count, 0)
        self.assertEqual(self.post('/api/admin/login/confirm/', {'code': code}).status_code, 200)

    def test_resend_cap_and_csrf(self):
        challenge = self.start()
        self.assertEqual(self.client.post('/api/admin/login/challenge/', {}).status_code, 403)
        LoginChallenge.objects.filter(pk=challenge.pk).update(resend_count=3, sent_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.post('/api/admin/login/challenge/').status_code, 429)
        self.assertEqual(len(mail.outbox), 1)

    def test_confirm_cannot_bypass_account_limit_with_fake_username(self):
        self.start()
        self.assertEqual(self.post('/api/admin/login/confirm/', {'username': 'first', 'code': 'bad'}).status_code, 400)
        bucket_count = LoginBucket.objects.count()
        for i in range(19):
            self.assertEqual(self.post('/api/admin/login/confirm/', {'username': str(i), 'code': 'bad'}).status_code, 400)
        self.assertEqual(self.post('/api/admin/login/confirm/', {'username': 'new', 'code': 'bad'}).status_code, 429)
        self.assertEqual(LoginBucket.objects.count(), bucket_count)


@override_settings(DEBUG=False)
class ErrorPageTests(TestCase):
    def test_missing_api_is_json_and_browser_is_branded(self):
        self.assertEqual(self.client.get('/api/missing/').status_code, 404)
        self.assertIn('detail', self.client.get('/api/missing/').json())
        response = self.client.get('/missing/')
        self.assertContains(response, 'STechPay', status_code=404)
        self.assertNotContains(response, 'Django', status_code=404)

    def test_error_templates_and_api_keep_status_without_diagnostics(self):
        for status in (400, 403, 404, 500):
            for path in ('/page/', '/api/page/'):
                response = errors.error_response(RequestFactory().get(path), status)
                self.assertEqual(response.status_code, status)
                self.assertNotIn(b'Traceback', response.content)
                if path.startswith('/api/'):
                    self.assertEqual(response['Content-Type'], 'application/json')

    def test_csrf_is_actionable_json(self):
        response = Client(enforce_csrf_checks=True).post('/api/admin/login/', {})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'csrf_failed')
