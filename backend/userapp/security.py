import hashlib
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.permissions import BasePermission

from .models import AuditEntry, LoginBucket, LoginChallenge

OWNER_COOKIE = 'checkout_owner'
OWNER_SALT = 'stechpay.checkout-owner.v1'
OWNER_AGE = 30 * 24 * 3600


def owner_hash(request):
    try:
        value = signing.loads(request.COOKIES.get(OWNER_COOKIE, ''), salt=OWNER_SALT, max_age=OWNER_AGE)
        return hashlib.sha256(value.encode()).hexdigest()
    except (signing.BadSignature, TypeError, AttributeError):
        return ''


def set_owner_cookie(request, response):
    if not owner_hash(request):
        response.set_cookie(OWNER_COOKIE, signing.dumps(secrets.token_urlsafe(32), salt=OWNER_SALT),
                            max_age=OWNER_AGE, httponly=True, secure=settings.SESSION_COOKIE_SECURE, samesite='Lax')


def admin_verified(request):
    return bool(request.user.is_authenticated and request.user.is_active and request.user.is_staff and
                (not settings.ADMIN_MFA_REQUIRED or request.session.get('admin_mfa_user') == request.user.pk))


def can_read_payment(request, payment):
    return bool((payment.owner_hash and secrets.compare_digest(payment.owner_hash, owner_hash(request))) or
                (admin_verified(request) and request.user.has_perm('userapp.view_payment')))


class AdminPermission(BasePermission):
    def has_permission(self, request, view):
        if not admin_verified(request):
            return False
        path = request.path
        model = 'payment'
        for segment, name in [('departments', 'department'), ('fees', 'fee'), ('session', 'sessionconfig')]:
            if f'/{segment}/' in path:
                model = name
        action = 'view' if request.method in ('GET', 'HEAD', 'OPTIONS') else 'change'
        if request.method == 'DELETE':
            action = 'delete'
        return request.user.has_perm(f'userapp.{action}_{model}')


def audit(request, action, target, changes=None):
    AuditEntry.objects.create(actor=request.user, action=action, target=str(target), changes=changes or {})


def _protected_view(request):
    return None


def client_ip(request):
    count = settings.REST_FRAMEWORK.get('NUM_PROXIES', 0)
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')
    if count and len(forwarded) >= count and forwarded[-count].strip():
        return forwarded[-count].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def login_limited(request):
    try:
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        username = str(data.get('username', '')).strip().casefold()[:150]
    except (ValueError, AttributeError):
        username = ''
    if request.path != '/api/admin/login/':
        import uuid
        try:
            challenge_id = uuid.UUID(request.session.get('login_challenge', ''))
            username = LoginChallenge.objects.filter(pk=challenge_id).values_list('user__username', flat=True).first() or ''
            username = username.strip().casefold()
        except (ValueError, TypeError, AttributeError):
            username = ''
    keys = [(f'ip:{client_ip(request)}', 20), (f'account:{username}', 5)]
    now = timezone.now()
    denied = False
    with transaction.atomic():
        for identity, limit in sorted(keys):
            key = salted_hmac('login-limit', identity).hexdigest()
            LoginBucket.objects.get_or_create(key=key, defaults={'window_start': now})
            bucket = LoginBucket.objects.select_for_update().get(key=key)
            if now - bucket.window_start >= timedelta(minutes=5):
                bucket.window_start, bucket.count = now, 0
            bucket.count += 1
            bucket.save(update_fields=['count', 'window_start'])
            denied = denied or bucket.count > limit
    return denied


class SecurityBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.csrf = CsrfViewMiddleware(get_response)

    def process_view(self, request, callback, callback_args, callback_kwargs):
        # Explicitly protect anonymous login and guest checkout, irrespective of DRF's exemption.
        if request.path.startswith('/api/') and request.path != '/api/payments/webhook/' and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            rejected = self.csrf.process_view(request, _protected_view, (), {})
            if rejected:
                return rejected
        if request.path == '/admin/login/':
            return HttpResponseRedirect('/?admin=1')
        if request.path.startswith('/admin/') and not admin_verified(request):
            return HttpResponseRedirect('/?admin=1')
        if request.path in ('/api/admin/login/', '/api/admin/login/confirm/', '/api/admin/login/challenge/') and request.method == 'POST':
            if login_limited(request):
                response = JsonResponse({'detail': 'Too many sign-in attempts. Try again in five minutes.'}, status=429)
                response['Retry-After'] = '300'
                return response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(('/api/', '/admin/')):
            response['Cache-Control'] = 'private, no-store'
            response['Referrer-Policy'] = 'no-referrer'
        return response
