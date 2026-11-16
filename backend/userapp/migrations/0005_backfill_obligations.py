from django.db import migrations


def backfill(apps, schema_editor):
    Payment = apps.get_model('userapp', 'Payment')
    Obligation = apps.get_model('userapp', 'PaymentObligation')
    db = schema_editor.connection.alias
    # Revoke sessions established before the authentication fixes (checkout cookies are separate).
    apps.get_model('sessions', 'Session').objects.using(db).all().delete()
    for payment in Payment.objects.using(db).order_by('created_at', 'pk').iterator():
        # Existing records are test payments, as confirmed before this migration.
        obligation, _ = Obligation.objects.using(db).get_or_create(
            matric_number=payment.matric_number, session_start=payment.session_start,
            mode=payment.mode, fee_type='department_dues')
        payment.obligation_id = obligation.pk
        # Historical approvals must be verified against Paystack before issuing new receipts.
        payment.allocation = 'review' if payment.channel == 'mock' else 'unallocated'
        if payment.channel == 'mock' and payment.status == 'successful':
            apps.get_model('userapp', 'AuditEntry').objects.using(db).create(
                action='payment.legacy_mock_invalidated', target=payment.reference,
                changes={'from': payment.status, 'to': 'failed'})
            payment.status = 'failed'
        payment.save(using=db, update_fields=['obligation', 'allocation', 'status'])
    for obligation in Obligation.objects.using(db).iterator():
        attempts = Payment.objects.using(db).filter(obligation_id=obligation.pk).order_by('-created_at', '-pk')
        active = attempts.filter(status='successful').first() or attempts.filter(status='pending').first() or attempts.first()
        obligation.active_attempt_id = active.pk if active else None
        obligation.save(using=db, update_fields=['active_attempt'])


class Migration(migrations.Migration):
    dependencies = [('userapp', '0004_payment_security'), ('sessions', '0001_initial')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
