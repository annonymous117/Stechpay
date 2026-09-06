from django.urls import path

from . import views

urlpatterns = [
    path("csrf/", views.csrf_view),
    path("meta/", views.meta_view),
    path("payments/initiate/", views.initiate_payment),
    path("payments/verify/", views.verify_payment),
    path("payments/webhook/", views.paystack_webhook),
    path("payments/<str:reference>/receipt/", views.receipt_pdf),
    path("admin/login/", views.admin_login),
    path("admin/logout/", views.admin_logout),
    path("admin/me/", views.admin_me),
    path("admin/stats/", views.admin_stats),
    path("admin/session/", views.admin_session),
    path("admin/receipts/verify/", views.admin_receipt_verify),
    path("admin/payments/export/", views.admin_payments_csv),
    path("admin/payments/", views.admin_payments),
    path("admin/departments/<int:dept_id>/", views.admin_department_detail),
    path("admin/departments/", views.admin_departments),
    path("admin/fees/<int:fee_id>/", views.admin_fee_detail),
    path("admin/fees/", views.admin_fees),
]

