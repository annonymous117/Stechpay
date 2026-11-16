from django.contrib import admin
from django import forms
from decimal import Decimal

from .models import Fee, Payment, SessionConfig, GatewayEvent, Notification, AuditEntry


class FeeForm(forms.ModelForm):
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=12, decimal_places=2)

    class Meta:
        model = Fee
        fields = '__all__'


@admin.register(Fee)
class FeeAdmin(admin.ModelAdmin):
    form = FeeForm
    list_display = ("department", "level", "amount", "description", "is_active", "updated_at")
    list_filter = ("department", "is_active")
    list_editable = ("amount", "is_active")
    search_fields = ("department",)
    ordering = ("department", "-level")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    list_display = (
        "matric_number",
        "full_name",
        "department_code",
        "level",
        "amount",
        "status",
        "reference",
        "email_sent",
        "paid_at",
    )
    list_filter = ("mode", "status", "allocation", "department_code", "level", "session_start")
    search_fields = ("matric_number", "full_name", "email", "reference", "receipt_id")
    readonly_fields = (
        "reference",
        "receipt_id",
        "authorization_url",
        "channel",
        "gateway_response",
        "email_sent",
        "created_at",
        "updated_at",
        "paid_at",
    )
    date_hierarchy = "created_at"


@admin.register(SessionConfig)
class SessionConfigAdmin(admin.ModelAdmin):
    list_display = ("session_start", "updated_at")
    fieldsets = [
        (
            None,
            {
                "fields": ("session_start",),
                "description": (
                    "Pin the active academic session, e.g. 2026 for 2026/27. "
                    "While a row exists the portal uses it for student level "
                    "calculation and receipts. Deleting this row reverts to "
                    "automatic detection (session starts in August)."
                ),
            },
        )
    ]

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not SessionConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GatewayEvent)
class GatewayEventAdmin(ReadOnlyAdmin):
    list_display = ('id', 'event_type', 'created_at', 'processed_at', 'attempts', 'error')


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdmin):
    list_display = ('payment', 'sent_at', 'attempts', 'error')


@admin.register(AuditEntry)
class AuditEntryAdmin(ReadOnlyAdmin):
    list_display = ('created_at', 'actor', 'action', 'target')
