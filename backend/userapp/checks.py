from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, register

from .paystack import configured_mode, PaystackError


@register(Tags.security, deploy=True)
def deployment_checks(app_configs, **kwargs):
    errors = []
    try:
        configured_mode()
    except PaystackError as exc:
        errors.append(Error(str(exc), id='userapp.E001'))
    if settings.APP_ENV not in ('staging', 'production'):
        errors.append(Error('Hosted deployments must set APP_ENV=staging or production.', id='userapp.E002'))
    if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
        errors.append(Error('Hosted deployments require PostgreSQL.', id='userapp.E003'))
    if not settings.ADMIN_MFA_REQUIRED:
        errors.append(Error('Hosted deployments require administrator MFA.', id='userapp.E004'))
    if settings.DEBUG or not all([settings.SECURE_SSL_REDIRECT, settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE, settings.SECURE_HSTS_SECONDS]):
        errors.append(Error('Configure HTTPS, secure cookies, HSTS and DEBUG=False.', id='userapp.E005'))
    if urlparse(settings.FRONTEND_URL).scheme != 'https':
        errors.append(Error('FRONTEND_URL must use HTTPS on hosted deployments.', id='userapp.E006'))
    if len(settings.SECRET_KEY) < 50 or settings.SECRET_KEY.startswith(('django-insecure-', 'REPLACE_')):
        errors.append(Error('Use a unique deployment secret with at least 50 characters.', id='userapp.E007'))
    if settings.PAYSTACK_MODE == 'live' and settings.APP_ENV != 'production':
        errors.append(Error('Live payments require APP_ENV=production.', id='userapp.E008'))
    if settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
        errors.append(Error('Configure SMTP for administrator sign-in codes and payment notifications.', id='userapp.E009'))
    return errors
