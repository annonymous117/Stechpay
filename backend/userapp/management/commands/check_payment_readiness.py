"""Read-only technical preflight; business approval and a pilot are still required."""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from userapp.checks import deployment_checks
from userapp.models import Department, Fee, GatewayEvent, Payment, SessionConfig
from userapp.paystack import configured_mode, PaystackError, verify_live_subaccount


class Command(BaseCommand):
    help = 'Check live payment prerequisites without changing records or charging anyone.'

    def add_arguments(self, parser):
        parser.add_argument('--verify-subaccounts', action='store_true',
                            help='Also make read-only Paystack API calls using the configured live key.')

    def handle(self, *args, **options):
        blockers = [f'{issue.id}: {issue.msg}' for issue in deployment_checks(None)]
        try:
            live = configured_mode() == 'live'
        except PaystackError:
            live = False
        if not live:
            blockers.append('Live credentials, production mode and explicit live enablement are not configured.')
        if not settings.PAYMENT_CHECKOUT_ENABLED:
            blockers.append('New payment checkouts are paused.')
        if not SessionConfig.is_manual():
            blockers.append('Confirm and pin the academic session in the administration dashboard.')
        departments = list(Department.objects.filter(is_active=True))
        if not departments:
            blockers.append('No active departments are configured.')
        for department in departments:
            code = department.live_subaccount_code.strip()
            if not code:
                blockers.append(f'{department.code}: live subaccount is missing.')
            elif options['verify_subaccounts'] and live:
                try:
                    verify_live_subaccount(code)
                    self.stdout.write(f'{department.code}: live NGN subaccount is active and accessible.')
                except PaystackError:
                    blockers.append(f'{department.code}: live subaccount could not be verified; check Paystack configuration or connectivity.')
            fees = Fee.objects.filter(department=department.code, is_active=True)
            if not fees.exists() or fees.filter(amount__lte=0).exists():
                blockers.append(f'{department.code}: configure valid fees before enabling collection.')
        cutoff = timezone.now() - timedelta(minutes=15)
        if GatewayEvent.objects.filter(processed_at__isnull=True, created_at__lt=cutoff).exists():
            blockers.append('Gateway events have been waiting over 15 minutes; investigate the payment worker.')
        if Payment.objects.filter(mode='live', allocation__in=('duplicate', 'review')).exists():
            blockers.append('Live payments need financial review before wider rollout.')
        if not options['verify_subaccounts']:
            self.stdout.write('Paystack account access was not checked. Use --verify-subaccounts for read-only API validation.')
        self.stdout.write('Still confirm each bank destination, split percentage, fee bearer and every intended department/level fee.')
        self.stdout.write('This check does not approve policies, merchant compliance, backups, monitoring or a live pilot.')
        if blockers:
            for blocker in blockers:
                self.stderr.write('- ' + blocker)
            raise CommandError(f'Live readiness has {len(blockers)} unresolved prerequisite(s).')
        self.stdout.write(self.style.SUCCESS('Technical prerequisites checked; complete the operational rollout checklist before public collection.'))
