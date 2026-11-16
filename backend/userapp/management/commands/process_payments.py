"""Run from a scheduled worker; no in-request threads or external refund actions."""
from datetime import timedelta
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction, close_old_connections
from django.db.models import Q, F
from django.utils import timezone

from userapp.emails import send_payment_confirmation
from userapp.models import GatewayEvent, Notification, Payment, LoginBucket, LoginChallenge
from userapp.payments import process_event, reconcile, recover_initialization


def deliver_notification(notification_id):
    now = timezone.now()
    with transaction.atomic():
        notice = Notification.objects.select_for_update().get(pk=notification_id)
        if notice.sent_at or notice.available_at > now or (notice.locked_until and notice.locked_until > now):
            return
        notice.locked_until = now + timedelta(minutes=5)
        notice.attempts += 1
        notice.save(update_fields=['locked_until', 'attempts'])
        payment_id = notice.payment_id
    payment = Payment.objects.get(pk=payment_id)
    sent = send_payment_confirmation(payment)
    with transaction.atomic():
        notice = Notification.objects.select_for_update().get(pk=notification_id)
        notice.locked_until = None
        if sent:
            notice.sent_at, notice.error = timezone.now(), ''
            Payment.objects.filter(pk=payment_id).update(email_sent=True)
        else:
            notice.error = 'Delivery failed or payment no longer eligible.'
            notice.available_at = timezone.now() + timedelta(minutes=min(2 ** min(notice.attempts, 10), 1440))
        notice.save()


class Command(BaseCommand):
    help = 'Process durable gateway events, reconcile payments and deliver queued confirmations.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--skip-reconcile', action='store_true')
        parser.add_argument('--skip-notifications', action='store_true')
        parser.add_argument('--watch', action='store_true', help='Run every minute for local development.')

    def handle(self, *args, **options):
        while True:
            self.run_once(options)
            if not options['watch']:
                break
            close_old_connections()
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                break

    def run_once(self, options):
        limit = max(1, min(options['limit'], 500))
        errors = 0
        for event_id in GatewayEvent.objects.filter(processed_at__isnull=True).order_by('attempts', 'pk').values_list('pk', flat=True)[:limit]:
            try:
                process_event(event_id)
            except Exception:
                # The event remains durable for retry; no credential/PII payloads enter logs.
                errors += 1
        if not options['skip_reconcile']:
            cutoff = timezone.now() - timedelta(minutes=15)
            ids = Payment.objects.filter(mode=settings.PAYSTACK_MODE).filter(
                Q(last_verified_at__isnull=True) | Q(last_verified_at__lt=cutoff)).order_by(
                    F('last_verified_at').asc(nulls_first=True), 'created_at').values_list('pk', flat=True)[:limit]
            for pk in ids:
                payment = Payment.objects.get(pk=pk)
                try:
                    reconcile(payment)
                except Exception:
                    errors += 1
                    Payment.objects.filter(pk=pk).update(last_verified_at=timezone.now())
                    try:
                        recover_initialization(payment)
                    except Exception:
                        self.stderr.write(f'Checkout recovery needs retry for payment ID {pk}.')
        notices = Notification.objects.none() if options['skip_notifications'] else Notification.objects.filter(sent_at__isnull=True, available_at__lte=timezone.now())
        for pk in notices.filter(
                Q(locked_until__isnull=True) | Q(locked_until__lte=timezone.now())).values_list('pk', flat=True)[:limit]:
            try:
                deliver_notification(pk)
            except Exception:
                errors += 1
        LoginBucket.objects.filter(window_start__lt=timezone.now() - timedelta(days=1)).delete()
        LoginChallenge.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
        self.stdout.write(f'Payment worker completed; items needing retry: {errors}')
