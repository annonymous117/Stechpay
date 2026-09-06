from django.contrib import admin

from .models import Fee, Payment, SessionConfig


@admin.register(Fee)
class FeeAdmin(admin.ModelAdmin):
    list_display = ("department", "level", "amount", "description", "is_active", "updated_at")
    list_filter = ("department", "is_active")
    list_editable = ("amount", "is_active")
    search_fields = ("department",)
    ordering = ("department", "-level")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
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
    list_filter = ("status", "department_code", "level", "session_start")
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
        return not SessionConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return True
