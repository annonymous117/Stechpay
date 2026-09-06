import uuid

from django.db import models

from .constants import DEFAULT_DEPARTMENTS, LEVEL_CHOICES, session_label, session_start_year


class Department(models.Model):
    code = models.CharField(max_length=10, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    subaccount_code = models.CharField(
        max_length=100,
        blank=True,
        help_text="Paystack Subaccount code (e.g. ACCT_xxxxxxxxx) to route department dues directly.",
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
                cls.objects.create(code=code, name=name, is_active=True)
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.matric_number} - {self.status} - NGN {self.amount}"

    @property
    def department(self):
        return self.department_code

    @property
    def department_display(self):
        return department_name(self.department_code)

