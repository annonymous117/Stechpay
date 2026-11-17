"""Email-verified, receipt-only access for students without accounts."""
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import AuditEntry, Payment, ReceiptRecovery
from .security import client_ip
from .serializers import VerificationSerializer
from .throttles import consume_limits

GENERIC_MESSAGE = 'If this reference has an eligible receipt, a code will be sent to the email used for payment. Delivery may take a minute.'


def email_hash(email):
    return salted_hmac('receipt-email', email.strip().lower()).hexdigest()


def challenge_id(request):
    try:
        return uuid.UUID(request.session.get('receipt_recovery', ''))
    except (ValueError, TypeError, AttributeError):
        return None


def expired_response():
    return Response({'detail': 'This verification has expired. Request a new code.', 'code': 'challenge_expired'}, status=400)


def public_challenge(challenge):
    return {'detail': GENERIC_MESSAGE, 'expires_at': challenge.expires_at.isoformat(),
            'resend_at': (challenge.requested_at + timedelta(seconds=60)).isoformat(),
            'resends_remaining': 3 - challenge.resend_count}


def ip_limit(request, operation, maximum):
    wait = consume_limits(f'receipt-{operation}', [(client_ip(request), maximum, 600)])
    if wait:
        response = Response({'detail': 'Too many receipt recovery requests. Please wait and try again.'}, status=429)
        response['Retry-After'] = str(wait)
        return response
    return None


@api_view(['POST'])
@permission_classes([AllowAny])
def recovery_request(request):
    serializer = VerificationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    limited = ip_limit(request, 'request', 10)
    if limited is not None:
        return limited
    reference = serializer.validated_data['reference']
    allowed = not consume_limits('receipt-reference', [(reference, 6, 3600)])
    # Queue every syntactically valid reference without looking up a payment here.
    # Neither response content nor SMTP timing discloses payment existence.
    now = timezone.now()
    challenge = ReceiptRecovery.objects.create(reference=reference, expires_at=now + timedelta(minutes=10),
                                               delivery_allowed=allowed, requested_at=now, available_at=now)
    previous = challenge_id(request)
    if previous:
        ReceiptRecovery.objects.filter(pk=previous).update(used=True, delivery_requested=False)
    request.session.cycle_key()
    request.session['receipt_recovery'] = str(challenge.pk)
    request.session.pop('receipt_grant', None)
    return Response(public_challenge(challenge), status=202)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def recovery_challenge(request):
    if request.method == 'GET':
        grant = request.session.get('receipt_grant') or {}
        payment = Payment.objects.filter(pk=grant.get('payment_id')).first()
        if payment and has_receipt_grant(request, payment) and payment.receipt_available:
            return Response({'receipt_url': f'/api/payments/{payment.reference}/receipt/',
                             'receipt_id': str(payment.receipt_id)})
    if request.method == 'POST':
        limited = ip_limit(request, 'resend', 10)
        if limited is not None:
            return limited
    with transaction.atomic():
        challenge = ReceiptRecovery.objects.select_for_update().filter(pk=challenge_id(request)).first()
        now = timezone.now()
        if not challenge or challenge.used or challenge.expires_at <= now or challenge.attempts >= 5:
            return expired_response()
        if request.method == 'POST':
            if challenge.resend_count >= 3 or challenge.requested_at + timedelta(seconds=60) > now:
                return Response({'detail': 'Please wait before requesting another code.'}, status=429)
            challenge.delivery_allowed = not consume_limits('receipt-reference', [(challenge.reference, 6, 3600)])
            challenge.requested_at = challenge.available_at = now
            challenge.resend_count += 1
            challenge.delivery_requested = True
            challenge.delivery_attempts = 0
            challenge.code_hash = ''
            challenge.save()
        return Response(public_challenge(challenge))


@api_view(['POST'])
@permission_classes([AllowAny])
def recovery_confirm(request):
    limited = ip_limit(request, 'confirm', 30)
    if limited is not None:
        return limited
    code = str(request.data.get('code', ''))
    with transaction.atomic():
        challenge = ReceiptRecovery.objects.select_for_update().filter(pk=challenge_id(request)).first()
        now = timezone.now()
        if not challenge or challenge.used or challenge.expires_at <= now or challenge.attempts >= 5:
            return expired_response()
        challenge.attempts += 1
        payment = Payment.objects.filter(pk=challenge.payment_id).first()
        valid = bool(len(code) == 6 and code.isascii() and code.isdigit() and challenge.code_hash
                     and secrets.compare_digest(challenge.code_hash, salted_hmac('receipt-code', code).hexdigest())
                     and payment and payment.receipt_available and payment.mode == settings.PAYSTACK_MODE
                     and secrets.compare_digest(challenge.email_hash, email_hash(payment.email)))
        challenge.used = valid
        challenge.save(update_fields=['attempts', 'used'])
        if not valid:
            return Response({'detail': 'The code could not be verified. Check the latest email and try again.'}, status=400)
        request.session.cycle_key()
        request.session['receipt_grant'] = {'payment_id': payment.pk, 'email_hash': challenge.email_hash,
                                            'expires_at': int((now + timedelta(minutes=10)).timestamp())}
        request.session.pop('receipt_recovery', None)
        AuditEntry.objects.create(action='receipt.recovered', target=str(payment.pk))
        return Response({'receipt_url': f'/api/payments/{payment.reference}/receipt/',
                         'receipt_id': str(payment.receipt_id)})


def has_receipt_grant(request, payment):
    grant = request.session.get('receipt_grant')
    return bool(isinstance(grant, dict) and grant.get('payment_id') == payment.pk
                and isinstance(grant.get('expires_at'), int) and grant['expires_at'] > timezone.now().timestamp()
                and payment.mode == settings.PAYSTACK_MODE
                and secrets.compare_digest(str(grant.get('email_hash', '')), email_hash(payment.email)))


def deliver_recovery(pk):
    """Called only by the scheduled worker; codes are never stored in plaintext."""
    with transaction.atomic():
        challenge = ReceiptRecovery.objects.select_for_update().get(pk=pk)
        now = timezone.now()
        if not challenge.delivery_requested or challenge.available_at > now:
            return
        payment = Payment.objects.filter(reference=challenge.reference, mode=settings.PAYSTACK_MODE,
                                         status=Payment.Status.SUCCESS, allocation='applied').first()
        if (challenge.used or challenge.expires_at <= now or challenge.attempts >= 5
                or challenge.delivery_attempts >= 3 or not challenge.delivery_allowed or not payment):
            challenge.delivery_requested = False
            challenge.save(update_fields=['delivery_requested'])
            return
        if consume_limits('receipt-mailbox', [(payment.email.strip().lower(), 8, 3600)]):
            challenge.delivery_requested = False
            challenge.save(update_fields=['delivery_requested'])
            return
        challenge.delivery_attempts += 1
        code = f'{secrets.randbelow(1000000):06d}'
        challenge.payment = payment
        challenge.email_hash = email_hash(payment.email)
        challenge.code_hash = salted_hmac('receipt-code', code).hexdigest()
        challenge.available_at = now + timedelta(minutes=2)
        challenge.save()
    # SMTP runs outside the row lock: public requests cannot infer existence by
    # waiting on an email delivery. The lease also prevents parallel deliveries.
    try:
        sent = send_mail('STechPay receipt recovery code',
                        f'Your receipt recovery code is {code}.\nReference: {payment.reference}\n'
                        f'Enter it on the receipt recovery page in the browser where you requested it. '
                        f'It expires at {challenge.expires_at:%H:%M UTC}.\n'
                        'If you did not request this code, you can ignore this email. Do not share the code.',
                        settings.DEFAULT_FROM_EMAIL, [payment.email])
    except Exception:
        sent = 0
    # A concurrent resend must keep its newly queued delivery and expiry.
    ReceiptRecovery.objects.filter(pk=pk, requested_at=challenge.requested_at, code_hash=challenge.code_hash).update(
        delivery_requested=not sent and challenge.delivery_attempts < 3,
        available_at=timezone.now() + timedelta(seconds=60))
    return bool(sent)
