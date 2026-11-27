"""Payment state transitions. Always lock the obligation before its attempt."""
import hashlib
import json
import secrets
from datetime import timedelta

from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, ValidationError

from . import paystack
from .models import AuditEntry, CheckoutRequest, GatewayEvent, Notification, Payment, PaymentObligation


class CheckoutConflict(APIException):
    status_code = 409
    default_detail = 'This payment requires review. Please contact the bursary.'


def reserve_checkout(data, parsed, fee, session_start, owner, key, subaccount):
    mode = paystack.configured_mode()
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        obligation, _ = PaymentObligation.objects.get_or_create(
            matric_number=parsed['normalized'], session_start=session_start, mode=mode)
        obligation = PaymentObligation.objects.select_for_update().get(pk=obligation.pk)
        prior = CheckoutRequest.objects.select_related('payment').filter(owner_hash=owner, key=key).first()
        if prior:
            if prior.request_hash != fingerprint or prior.payment.obligation_id != obligation.pk:
                raise CheckoutConflict('This request key was already used for different payment details.')
            return prior.payment, False
        paid = obligation.attempts.filter(allocation='applied').first()
        if paid:
            if paid.owner_hash != owner:
                raise CheckoutConflict('This payment cannot be started in this browser. Contact the bursary for assistance.')
            CheckoutRequest.objects.create(owner_hash=owner, key=key, request_hash=fingerprint, payment=paid)
            return paid, False
        if obligation.active_attempt_id:
            active = Payment.objects.select_for_update().get(pk=obligation.active_attempt_id)
            if active.status != Payment.Status.FAILED:
                if active.owner_hash != owner:
                    raise CheckoutConflict('This payment cannot be started in this browser. Contact the bursary for assistance.')
                if active.request_hash != fingerprint:
                    raise CheckoutConflict('An existing checkout has different payer details. Contact the bursary to correct it.')
                CheckoutRequest.objects.create(owner_hash=owner, key=key, request_hash=fingerprint, payment=active)
                return active, False
        payment = Payment.objects.create(
            **data, department_code=parsed['department_code'], level=parsed['level'],
            session_start=session_start, amount=fee.amount, mode=mode, obligation=obligation,
            owner_hash=owner, idempotency_key=key, request_hash=fingerprint,
            reference=f'STECH-{mode.upper()}-{secrets.token_hex(16).upper()}', subaccount_code=subaccount)
        obligation.active_attempt = payment
        obligation.save(update_fields=['active_attempt'])
        CheckoutRequest.objects.create(owner_hash=owner, key=key, request_hash=fingerprint, payment=payment)
        return payment, True


def _validate(payment, raw):
    if not isinstance(raw, dict):
        raise ValidationError('Invalid gateway transaction.')
    if raw.get('reference') != payment.reference or raw.get('domain') != payment.mode:
        raise ValidationError('Gateway reference or payment mode does not match.')
    if type(raw.get('amount')) is not int or raw['amount'] != paystack.minor_units(payment.amount) or raw.get('currency') != 'NGN':
        raise ValidationError('Gateway amount or currency does not match.')
    identifier = raw.get('id')
    if type(identifier) is not int or identifier <= 0:
        raise ValidationError('Missing gateway transaction ID.')
    if payment.provider_id and payment.provider_id != str(identifier):
        raise ValidationError('Gateway transaction ID changed.')
    customer = raw.get('customer') or {}
    if not isinstance(customer, dict) or str(customer.get('email', '')).lower() != payment.email.lower():
        raise ValidationError('Gateway customer does not match.')
    if payment.subaccount_code:
        sub = raw.get('subaccount') or {}
        code = sub.get('subaccount_code') if isinstance(sub, dict) else sub
        if code != payment.subaccount_code:
            raise ValidationError('Gateway settlement destination does not match.')


def apply_verification(payment, raw):
    if not payment.obligation_id:
        raise ValidationError('Payment must be migrated before verification.')
    with transaction.atomic():
        PaymentObligation.objects.select_for_update().get(pk=payment.obligation_id)
        current = Payment.objects.select_for_update().get(pk=payment.pk)
        _validate(current, raw)
        status = raw.get('status')
        before = current.status
        current.last_verified_at = timezone.now()
        current.provider_id = str(raw['id'])
        current.gateway_response = str(raw.get('gateway_response', ''))[:500]
        current.channel = str(raw.get('channel', ''))[:50]
        if status == 'success' and current.status not in (Payment.Status.REFUNDED, Payment.Status.REVERSED, Payment.Status.DISPUTED):
            if current.status != Payment.Status.SUCCESS or current.allocation == 'unallocated':
                current.status = Payment.Status.SUCCESS
                try:
                    paid = parse_datetime(str(raw.get('paid_at', '')))
                except ValueError:
                    paid = None
                current.paid_at = paid if paid and timezone.is_aware(paid) else timezone.now()
                other = Payment.objects.filter(obligation_id=current.obligation_id, allocation='applied').exclude(pk=current.pk).exists()
                current.allocation = 'duplicate' if other else 'applied'
            if current.allocation == 'applied' and not current.email_sent:
                Notification.objects.get_or_create(payment=current)
        elif status in ('failed', 'abandoned') and current.status == Payment.Status.PENDING:
            current.status = Payment.Status.FAILED
        elif status == 'reversed':
            current.status, current.allocation = Payment.Status.REVERSED, 'review'
        current.save()
        if current.status != before:
            AuditEntry.objects.create(action='payment.transition', target=current.reference,
                                      changes={'from': before, 'to': current.status, 'allocation': current.allocation, 'provider_id': current.provider_id})
        return current


def reconcile(payment):
    if payment.mode != paystack.configured_mode():
        raise paystack.PaystackError('Payment belongs to a different gateway mode.')
    outcome = paystack.verify_transaction(payment.reference)
    return apply_verification(payment, outcome['raw'])


def initialization_failed(payment, error):
    with transaction.atomic():
        PaymentObligation.objects.select_for_update().get(pk=payment.obligation_id)
        current = Payment.objects.select_for_update().get(pk=payment.pk)
        if current.status == Payment.Status.PENDING:
            current.initialization_error = True
            current.gateway_response = 'Initialization outcome unknown; reconciliation required'
            current.save(update_fields=['initialization_error', 'gateway_response', 'status'])


def recover_initialization(payment):
    """Retry only the reserved reference. Never mint a new reference after an ambiguous timeout."""
    if payment.status != Payment.Status.PENDING or payment.authorization_url or payment.created_at > timezone.now() - timedelta(minutes=1):
        return
    result = paystack.initialize_transaction(
        email=payment.email, amount=payment.amount, reference=payment.reference,
        callback_url=settings.FRONTEND_URL.rstrip('/') + '/callback',
        metadata={'matric_number': payment.matric_number, 'session_start': payment.session_start},
        subaccount=payment.subaccount_code or None)
    Payment.objects.filter(pk=payment.pk, authorization_url='').update(authorization_url=result['authorization_url'])


def sanitized_event(payload):
    data = payload.get('data')
    if not isinstance(data, dict):
        raise ValidationError('Invalid webhook data.')
    # Do not persist card authorization objects or arbitrary webhook PII.
    fields = ('id', 'reference', 'status', 'domain', 'amount', 'currency', 'paid_at', 'channel', 'gateway_response', 'resolution')
    result = {key: data[key] for key in fields if key in data}
    for key, names in [('customer', ('email',)), ('subaccount', ('subaccount_code',)), ('transaction', ('id', 'reference', 'domain'))]:
        value = data.get(key)
        if isinstance(value, dict):
            result[key] = {name: value[name] for name in names if name in value}
        elif isinstance(value, (str, int)):
            result[key] = value
    return {'event': payload['event'], 'data': result}


def process_event(event_id):
    event = GatewayEvent.objects.get(pk=event_id)
    if event.processed_at:
        return
    data = event.payload['data']
    name = event.event_type
    tx = data.get('transaction')
    reference = data.get('reference') if name == 'charge.success' else (tx.get('reference') if isinstance(tx, dict) else None)
    payment = Payment.objects.filter(reference=reference).first() if reference else None
    if not payment and tx:
        identifier = tx.get('id') if isinstance(tx, dict) else tx
        payment = Payment.objects.filter(provider_id=str(identifier), mode=paystack.configured_mode()).first()
    try:
        if name == 'charge.success' and payment:
            if data.get('status') != 'success':
                raise ValidationError('Invalid success event.')
            _validate(payment, data)
            # Treat the signed event as a trigger; fetch current gateway state before
            # issuing a receipt. An API failure leaves the durable event for retry.
            reconcile(payment)
        elif name.startswith(('refund.', 'charge.dispute.')):
            if not payment:
                raise ValidationError('Financial event needs transaction reconciliation.')
            # Refresh authoritative transaction identity before applying signed financial events.
            reconcile(payment)
            with transaction.atomic():
                PaymentObligation.objects.select_for_update().get(pk=payment.obligation_id)
                current = Payment.objects.select_for_update().get(pk=payment.pk)
                if name == 'refund.processed':
                    current.status, current.allocation = Payment.Status.REFUNDED, 'review'
                elif name.startswith('charge.dispute.'):
                    current.status, current.allocation = Payment.Status.DISPUTED, 'review'
                else:
                    current.allocation = 'review'
                # Even partial refunds/resolved disputes require explicit bursary review.
                current.save(update_fields=['status', 'allocation'])
                AuditEntry.objects.create(action=name, target=current.reference, changes={'event': event.digest})
        elif name == 'charge.success':
            raise ValidationError('Unknown payment reference.')
        GatewayEvent.objects.filter(pk=event.pk).update(processed_at=timezone.now(), error='')
    except Exception as exc:
        from django.db.models import F
        detail = str(exc) if isinstance(exc, (paystack.PaystackError, ValidationError)) else type(exc).__name__
        GatewayEvent.objects.filter(pk=event.pk).update(attempts=F('attempts') + 1, error=detail[:200])
        raise
