import uuid

from django.db import models
from django.utils import timezone

from .constants import DEFAULT_DEPARTMENTS, LEVEL_CHOICES, session_label, session_start_year


class Department(models.Model):
    code = models.CharField(max_length=10, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    subaccount_code = models.CharField(
        max_length=100,
        blank=True,
        help_text="Test-mode Paystack subaccount. Never used for live payments.",
    )
    live_subaccount_code = models.CharField(
        max_length=100, blank=True,
        help_text="Live-mode Paystack subaccount. Required for live departmental payments.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @classmethod
    def get_active_map(cls):
        """Returns dict of code -> name for active departments, auto-seeding if empty."""
        departments = list(cls.objects.filter(is_active=True))
        if not departments and not cls.objects.exists():
            for code, name in DEFAULT_DEPARTMENTS.items():
                cls.objects.get_or_create(code=code, defaults={"name": name, "is_active": True})
            departments = list(cls.objects.filter(is_active=True))
        return {d.code: d.name for d in departments}


def department_name(code):
    dept = Department.objects.filter(code=code.upper()).first()
    if dept:
        return dept.name
    return DEFAULT_DEPARTMENTS.get(code.upper(), code.upper())


class SessionConfig(models.Model):
    """Admin-pinned active session.

    Exactly one row (pk=1) means the session is set manually; no row means
    the start year is derived automatically from today's date (August cutoff).
    Deleting the row reverts to automatic mode.
    """

    session_start = models.PositiveIntegerField(
        help_text="First calendar year of the active session, e.g. 2026 for 2026/27."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "Session configuration"

    def __str__(self):
        return session_label(self.session_start)

    @classmethod
    def is_manual(cls):
        return cls.objects.exists()

    @classmethod
    def active_start(cls):
        row = cls.objects.first()
        return row.session_start if row else session_start_year()


class Fee(models.Model):
    department = models.CharField(max_length=10)
    level = models.PositiveIntegerField(choices=LEVEL_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=200, default="Departmental Dues")
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["department", "level"], name="unique_fee_per_dept_level")
        ]
        ordering = ["department", "-level"]

    def __str__(self):
        return f"{self.department} {self.level}L - NGN {self.amount}"


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "successful", "Successful"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        REVERSED = "reversed", "Reversed"
        DISPUTED = "disputed", "Disputed"

    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    matric_number = models.CharField(max_length=20, db_index=True)
    department_code = models.CharField(max_length=10)
    level = models.PositiveIntegerField()
    session_start = models.PositiveIntegerField(default=session_start_year)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reference = models.CharField(max_length=100, unique=True)
    authorization_url = models.URLField(blank=True)
    channel = models.CharField(max_length=50, blank=True)
    subaccount_code = models.CharField(max_length=100, blank=True)
    gateway_response = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    receipt_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    email_sent = models.BooleanField(default=False)
    mode = models.CharField(max_length=4, default="test", choices=[("test", "Test"), ("live", "Live")])
    obligation = models.ForeignKey("PaymentObligation", null=True, on_delete=models.PROTECT, related_name="attempts")
    owner_hash = models.CharField(max_length=64, blank=True)
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    request_hash = models.CharField(max_length=64, blank=True)
    provider_id = models.CharField(max_length=100, null=True, blank=True)
    allocation = models.CharField(max_length=12, default="unallocated", choices=[
        ("unallocated", "Unallocated"), ("applied", "Applied"), ("duplicate", "Duplicate charge"), ("review", "Needs review")])
    last_verified_at = models.DateTimeField(null=True, blank=True)
    initialization_error = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner_hash", "idempotency_key"], name="unique_checkout_request"),
            models.UniqueConstraint(fields=["mode", "provider_id"], name="unique_provider_payment"),
            models.UniqueConstraint(fields=["obligation"], condition=models.Q(allocation="applied"), name="one_applied_payment"),
        ]

    def __str__(self):
        return f"{self.matric_number} - {self.status} - NGN {self.amount}"

    @property
    def department(self):
        return self.department_code

    @property
    def department_display(self):
        return department_name(self.department_code)

    @property
    def receipt_available(self):
        return self.status == self.Status.SUCCESS and self.allocation == "applied"


class PaymentObligation(models.Model):
    matric_number = models.CharField(max_length=20)
    session_start = models.PositiveIntegerField()
    mode = models.CharField(max_length=4)
    fee_type = models.CharField(max_length=30, default="department_dues")
    active_attempt = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["matric_number", "session_start", "mode", "fee_type"], name="unique_dues_obligation")]


class GatewayEvent(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)


class CheckoutRequest(models.Model):
    owner_hash = models.CharField(max_length=64)
    key = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner_hash', 'key'], name='unique_idempotency_key')]


class Notification(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True)


class LoginBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)


class LoginChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64)
    auth_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    used = models.BooleanField(default=False)
    sent_at = models.DateTimeField(default=timezone.now)
    resend_count = models.PositiveIntegerField(default=0)


class AuditEntry(models.Model):
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=100)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class ReceiptRecovery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=100)
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.SET_NULL)
    email_hash = models.CharField(max_length=64, blank=True)
    code_hash = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    requested_at = models.DateTimeField(default=timezone.now)
    available_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveIntegerField(default=0)
    resend_count = models.PositiveIntegerField(default=0)
    delivery_attempts = models.PositiveIntegerField(default=0)
    delivery_requested = models.BooleanField(default=True)
    delivery_allowed = models.BooleanField(default=True)
    used = models.BooleanField(default=False)

