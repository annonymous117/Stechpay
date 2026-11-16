"""Disposable test databases and dummy services; never deploy this settings module."""
import os
os.environ.setdefault('DJANGO_SECRET_KEY', 'isolated-test-key-not-for-deployment-000000000000000000000')
from .settings import *  # noqa: F403

DEBUG = False
SECRET_KEY = 'isolated-test-key-not-a-deployment-secret-00000000000000000000'
ALLOWED_HOSTS = ['testserver', 'localhost']
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PAYSTACK_SECRET_KEY = 'sk_test_' + 'a' * 40
PAYSTACK_MODE = 'test'
ALLOW_LIVE_PAYMENTS = False
PAYSTACK_MOCK_MODE = False
PAYSTACK_WEBHOOK_SECRETS = []
ADMIN_MFA_REQUIRED = False
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
                         'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}
TEST_SQLITE_FILE = env('TEST_SQLITE_FILE')
if TEST_SQLITE_FILE:
    DATABASES['default']['TEST'] = {'NAME': TEST_SQLITE_FILE}
if env('TEST_POSTGRES_DB'):
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': env('TEST_POSTGRES_DB'),
        'USER': env('TEST_POSTGRES_USER', 'postgres'), 'PASSWORD': env('TEST_POSTGRES_PASSWORD'),
        'HOST': env('TEST_POSTGRES_HOST', 'localhost'), 'PORT': env('TEST_POSTGRES_PORT', '5432'),
    }}
