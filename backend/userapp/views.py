import csv
import hashlib
import hmac
import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"

from . import paystack
from .constants import (
    LEVEL_CHOICES,
    MAX_LEVEL,
    MIN_LEVEL,
    department_name,
    session_label,
    session_start_year,
)
from .emails import send_payment_confirmation_async
from .matric import normalize_matric, parse_matric
from .models import Department, Fee, Payment, SessionConfig
from .receipts import generate_receipt_pdf

CALLBACK_PATH = "/callback"


def _callback_url(request):
    return f"{request.scheme}://{request.get_host()}{CALLBACK_PATH}"


def _payment_summary(payment):
    return {
        "reference": payment.reference,
        "receipt_id": str(payment.receipt_id),
        "full_name": payment.full_name,
        "email": payment.email,
        "matric_number": payment.matric_number,
        "department_code": payment.department_code,
        "department": payment.department_display,
        "level": payment.level,
        "session": f"{payment.session_start}/{str(payment.session_start + 1)[2:]}",
        "amount": str(payment.amount),
        "status": payment.status,
        "channel": payment.channel,
        "subaccount_code": payment.subaccount_code,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "receipt_url": (
            f"/api/payments/{payment.reference}/receipt/?code={payment.receipt_id}"
            if payment.status == Payment.Status.SUCCESS
            else None
        ),
    }


def _fulfill_payment(payment, outcome_raw):
    """Atomically validates and marks a payment as successful."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status == Payment.Status.SUCCESS:
            return True, payment

        # Amount and Currency Validation
        expected_kobo = int(round(float(payment.amount) * 100))
        paid_kobo = outcome_raw.get("amount")
        paid_currency = outcome_raw.get("currency")

        if not paystack.is_mock_mode():
            if paid_kobo is not None and int(paid_kobo) != expected_kobo:
                payment.status = Payment.Status.FAILED
                payment.gateway_response = f"Amount mismatch: expected {expected_kobo} kobo, got {paid_kobo} kobo"
                payment.save()
                return False, payment
            if paid_currency is not None and str(paid_currency).upper() != "NGN":
                payment.status = Payment.Status.FAILED
                payment.gateway_response = f"Currency mismatch: expected NGN, got {paid_currency}"
                payment.save()
                return False, payment

        payment.gateway_response = str(outcome_raw.get("gateway_response", "Approved"))[:500]
        if outcome_raw.get("channel"):
            payment.channel = str(outcome_raw["channel"])[:50]

        paid_at_raw = outcome_raw.get("paid_at")
        payment.paid_at = timezone.now()
        if paid_at_raw:
            if isinstance(paid_at_raw, (int, float)):
                from datetime import datetime
                payment.paid_at = timezone.make_aware(datetime.fromtimestamp(paid_at_raw))
            elif isinstance(paid_at_raw, str):
                try:
                    from dateutil.parser import parse
                    payment.paid_at = parse(paid_at_raw)
                except Exception:
                    pass

        payment.status = Payment.Status.SUCCESS
        payment.save()

        # Non-blocking async email dispatch
        send_payment_confirmation_async(payment.pk)
        return True, payment


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def csrf_view(request):
    return Response({"detail": "CSRF cookie set."})


@api_view(["GET"])
@permission_classes([AllowAny])
def meta_view(request):
    active_dept_map = Department.get_active_map()
    fees = Fee.objects.filter(is_active=True)
    fee_map = {(f.department, f.level): str(f.amount) for f in fees}
    session_start = SessionConfig.active_start()
    return Response(
        {
            "session": session_label(session_start),
            "session_start": session_start,
            "departments": [
                {"code": code, "name": name}
                for code, name in sorted(active_dept_map.items())
            ],
            "levels": [lvl for lvl, _ in LEVEL_CHOICES],
            "min_level": MIN_LEVEL,
            "max_level": MAX_LEVEL,
            "fees": [
                {
                    "department": f.department,
                    "level": f.level,
                    "amount": str(f.amount),
                    "description": f.description,
                }
                for f in fees
            ],
            "fee_map": {f"{d}/{l}": a for (d, l), a in fee_map.items()},
            "paystack_mock_mode": paystack.is_mock_mode(),
        }
    )


def _get_fee_or_error(department_code, level):
    try:
        return Fee.objects.get(department=department_code, level=level, is_active=True)
    except Fee.DoesNotExist:
        raise ValidationError(
            {
                "detail": (
                    f"No active fee configured for {department_name(department_code)} "
                    f"{level} Level. Please contact the bursary."
                )
            }
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def initiate_payment(request):
    data = request.data or {}
    full_name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    raw_matric = str(data.get("matric_number", "")).strip()

    errors = {}
    if len(full_name) < 3:
        errors["full_name"] = "Enter the student's full name."
    if "@" not in email or "." not in email.split("@")[-1]:
        errors["email"] = "Enter a valid email address."
    if errors:
        raise ValidationError(errors)

    parsed = parse_matric(raw_matric)
    fee = _get_fee_or_error(parsed["department_code"], parsed["level"])
    matric = parsed["normalized"]

    session_start = SessionConfig.active_start()
    existing = Payment.objects.filter(
        matric_number=matric,
        session_start=session_start,
        status=Payment.Status.SUCCESS,
    ).first()
    if existing:
        return Response({"already_paid": True, "payment": _payment_summary(existing)})

    # Re-use active pending payment initiated within the last 15 minutes to avoid duplicates
    cutoff = timezone.now() - timezone.timedelta(minutes=15)
    recent_pending = (
        Payment.objects.filter(
            matric_number=matric,
            session_start=session_start,
            department_code=parsed["department_code"],
            level=parsed["level"],
            status=Payment.Status.PENDING,
            created_at__gte=cutoff,
        )
        .exclude(authorization_url="")
        .first()
    )
    if recent_pending:
        return Response(
            {
                "already_paid": False,
                "authorization_url": recent_pending.authorization_url,
                "reference": recent_pending.reference,
                "resumed": True,
            }
        )

    # Cancel/supersede any older pending attempts for this matric in this session
    Payment.objects.filter(
        matric_number=matric,
        session_start=session_start,
        status=Payment.Status.PENDING,
    ).update(
        status=Payment.Status.FAILED,
        gateway_response="Superseded by a new payment attempt",
    )

    dept = Department.objects.filter(code=parsed["department_code"]).first()
    subaccount_code = dept.subaccount_code.strip() if dept and dept.subaccount_code else ""

    reference = f"STECH-{secrets.token_hex(6).upper()}-{int(timezone.now().timestamp())}"
    payment = Payment.objects.create(
        full_name=full_name,
        email=email,
        matric_number=matric,
        department_code=parsed["department_code"],
        level=parsed["level"],
        session_start=session_start,
        amount=fee.amount,
        reference=reference,
        subaccount_code=subaccount_code,
    )

    try:
        result = paystack.initialize_transaction(
            email=email,
            amount=fee.amount,
            reference=reference,
            callback_url=_callback_url(request),
            metadata={
                "full_name": full_name,
                "matric_number": matric,
                "department": parsed["department_code"],
                "level": parsed["level"],
            },
            subaccount=subaccount_code or None,
        )
        payment.authorization_url = result["authorization_url"]
        payment.save(update_fields=["authorization_url"])
    except paystack.PaystackError as exc:
        payment.status = Payment.Status.FAILED
        payment.gateway_response = f"Gateway initialization failed: {exc}"[:500]
        payment.save(update_fields=["status", "gateway_response"])
        return Response(
            {"detail": f"Could not initiate transaction with payment provider: {exc}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    except Exception as exc:
        payment.status = Payment.Status.FAILED
        payment.gateway_response = f"Initialization error: {exc}"[:500]
        payment.save(update_fields=["status", "gateway_response"])
        raise

    return Response(
        {"already_paid": False, "authorization_url": result["authorization_url"], "reference": reference}
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def verify_payment(request):
    reference = str((request.data or {}).get("reference", "")).strip()
    if not reference:
        raise ValidationError({"reference": "Payment reference is required."})

    try:
        payment = Payment.objects.get(reference=reference)
    except Payment.DoesNotExist:
        return Response({"detail": "Unknown payment reference."}, status=status.HTTP_404_NOT_FOUND)

    if payment.status != Payment.Status.SUCCESS:
        outcome = paystack.verify_transaction(reference)
        if outcome["status"] == "success":
            _fulfill_payment(payment, outcome["raw"])
            payment.refresh_from_db()
        elif outcome["status"] == "failed":
            payment.status = Payment.Status.FAILED
            payment.gateway_response = str(outcome["raw"].get("gateway_response", "Failed"))[:500]
            payment.save()

    return Response({"payment": _payment_summary(payment)})


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def paystack_webhook(request):
    """Paystack Webhook handler with HMAC-SHA512 signature validation."""
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not secret and not paystack.is_mock_mode():
        return Response({"status": "Paystack secret key not set"}, status=400)

    # In production, verify HMAC signature
    if secret:
        signature = request.headers.get("x-paystack-signature") or request.META.get("HTTP_X_PAYSTACK_SIGNATURE")
        raw_body = request.body
        computed_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
        if not signature or not hmac.compare_digest(signature, computed_sig):
            return Response({"detail": "Invalid signature"}, status=400)

    event_payload = request.data or {}
    event_type = event_payload.get("event")

    if event_type == "charge.success":
        data = event_payload.get("data", {})
        reference = data.get("reference")
        if reference:
            payment = Payment.objects.filter(reference=reference).first()
            if payment and payment.status != Payment.Status.SUCCESS:
                _fulfill_payment(payment, data)

    return Response({"status": "event processed"}, status=200)


@api_view(["GET"])
@permission_classes([AllowAny])
def receipt_pdf(request, reference):
    try:
        receipt_code = uuid.UUID(str(request.query_params.get("code", "")))
    except ValueError:
        return HttpResponse("Receipt not found.", status=404)
    try:
        payment = Payment.objects.get(reference=reference, receipt_id=receipt_code)
    except Payment.DoesNotExist:
        return HttpResponse("Receipt not found.", status=404)
    if payment.status != Payment.Status.SUCCESS:
        return HttpResponse("Receipt available only after successful payment.", status=403)

    pdf_bytes = generate_receipt_pdf(payment)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"receipt-{payment.matric_number.replace('/', '-')}-{payment.session_start}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def admin_login(request):
    username = str((request.data or {}).get("username", "")).strip()
    password = str((request.data or {}).get("password", ""))
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_staff:
        return Response({"detail": "Invalid credentials or not an administrator."}, status=400)
    login(request, user)
    return Response({"username": user.username, "is_staff": True})


@api_view(["POST"])
@permission_classes([AllowAny])
def admin_logout(request):
    logout(request)
    return Response({"detail": "Logged out."})


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_me(request):
    if request.user.is_authenticated and request.user.is_staff:
        return Response({"authenticated": True, "username": request.user.username})
    return Response({"authenticated": False})


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_stats(request):
    success_q = Q(status=Payment.Status.SUCCESS)
    totals = Payment.objects.aggregate(
        total_collected=Sum("amount", filter=success_q),
        successful=Count("id", filter=success_q),
        pending=Count("id", filter=Q(status=Payment.Status.PENDING)),
        failed=Count("id", filter=Q(status=Payment.Status.FAILED)),
    )

    departments = Department.objects.all()

    by_dept_raw = list(
        Payment.objects.filter(success_q)
        .values("department_code")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    by_dept_data = {r["department_code"]: r for r in by_dept_raw}

    # Dynamic palette for multi-color chart
    PALETTE = [
        "#0f4c81", "#2a9d8f", "#e76f51", "#f4a261", "#8338ec", "#3a86ff",
        "#06d6a0", "#118ab2", "#e63946", "#ffb703", "#588157", "#3d5a80"
    ]

    by_department = []
    chart_data = []
    for idx, d in enumerate(departments):
        record = by_dept_data.get(d.code, {"total": Decimal("0.00"), "count": 0})
        dept_item = {
            "department_code": d.code,
            "department_name": d.name,
            "total": str(record["total"] or "0"),
            "count": record["count"],
            "color": PALETTE[idx % len(PALETTE)],
        }
        by_department.append(dept_item)
        chart_data.append(dept_item)

    by_department.sort(key=lambda x: Decimal(x["total"]), reverse=True)

    by_level = list(
        Payment.objects.filter(success_q)
        .values("level")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("level")
    )

    return Response(
        {
            "total_collected": str(totals["total_collected"] or "0"),
            "successful": totals["successful"] or 0,
            "pending": totals["pending"] or 0,
            "failed": totals["failed"] or 0,
            "by_department": by_department,
            "by_level": by_level,
            "chart_data": chart_data,
        }
    )


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_payments(request):
    qs = Payment.objects.all()
    status_filter = request.query_params.get("status", "")
    if status_filter in Payment.Status.values:
        qs = qs.filter(status=status_filter)
    dept = request.query_params.get("department", "")
    if dept:
        qs = qs.filter(department_code__iexact=dept)
    query = request.query_params.get("q", "").strip()
    if query:
        normalized_query = normalize_matric(query)
        filter_q = Q(full_name__icontains=query) | Q(email__icontains=query)
        for term in set([query, normalized_query]):
            filter_q |= Q(matric_number__icontains=term)
        qs = qs.filter(filter_q)

    limit = min(int(request.query_params.get("limit", "200") or 200), 1000)
    payments = [dict(_payment_summary(p)) | {"created_at": p.created_at.isoformat()} for p in qs[:limit]]
    return Response({"count": qs.count(), "payments": payments})


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_payments_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="stechpay-payments-report.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Reference",
            "Receipt ID",
            "Name",
            "Email",
            "Matric Number",
            "Department",
            "Level",
            "Session",
            "Amount (NGN)",
            "Status",
            "Channel",
            "Subaccount",
            "Paid At",
        ]
    )
    for p in Payment.objects.all():
        writer.writerow(
            [
                p.reference,
                p.receipt_id,
                p.full_name,
                p.email,
                p.matric_number,
                p.department_display,
                f"{p.level}L",
                f"{p.session_start}/{p.session_start + 1}",
                p.amount,
                p.status,
                p.channel or "-",
                p.subaccount_code or "-",
                p.paid_at.strftime("%Y-%m-%d %H:%M:%S") if p.paid_at else "-",
            ]
        )
    return response


# ------------------------ Department Management ------------------------ #

def _department_json(d):
    return {
        "id": d.id,
        "code": d.code,
        "name": d.name,
        "subaccount_code": d.subaccount_code,
        "is_active": d.is_active,
    }


@api_view(["GET", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_departments(request):
    if request.method == "GET":
        Department.get_active_map()  # ensures default seeding if table empty
        return Response({"departments": [_department_json(d) for d in Department.objects.all()]})

    data = request.data or {}
    code = str(data.get("code", "")).strip().upper()
    name = str(data.get("name", "")).strip()
    subaccount_code = str(data.get("subaccount_code", "")).strip()
    is_active = bool(data.get("is_active", True))

    errors = {}
    if not code or len(code) < 2 or len(code) > 10:
        errors["code"] = "Enter a valid department code (2-10 letters, e.g. CSC, ICH)."
    if not name or len(name) < 3:
        errors["name"] = "Enter a descriptive department/course name."
    if Department.objects.filter(code=code).exists():
        errors["code"] = f"Department code '{code}' already exists."

    if errors:
        raise ValidationError(errors)

    dept = Department.objects.create(
        code=code,
        name=name,
        subaccount_code=subaccount_code,
        is_active=is_active,
    )
    return Response({"department": _department_json(dept)}, status=201)


@api_view(["PATCH", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_department_detail(request, dept_id):
    try:
        dept = Department.objects.get(pk=dept_id)
    except Department.DoesNotExist:
        return Response({"detail": "Department not found."}, status=404)

    if request.method == "DELETE":
        dept.delete()
        return Response(status=204)

    data = request.data or {}
    if "name" in data:
        name = str(data["name"]).strip()
        if len(name) >= 3:
            dept.name = name
    if "subaccount_code" in data:
        dept.subaccount_code = str(data["subaccount_code"]).strip()
    if "is_active" in data:
        dept.is_active = bool(data["is_active"])
    dept.save()
    return Response({"department": _department_json(dept)})


# ----------------------------- Fee Management --------------------------- #

def _fee_json(fee):
    return {
        "id": fee.id,
        "department": fee.department,
        "department_name": department_name(fee.department),
        "level": fee.level,
        "amount": str(fee.amount),
        "description": fee.description,
        "is_active": fee.is_active,
    }


def _parse_amount(value):
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError
    return amount


@api_view(["GET", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_fees(request):
    if request.method == "GET":
        return Response({"fees": [_fee_json(f) for f in Fee.objects.all()]})

    data = request.data or {}
    errors = {}
    dept_code = str(data.get("department", "")).strip().upper()
    active_depts = Department.get_active_map()
    if dept_code not in active_depts:
        errors["department"] = "Choose a valid active department."
    try:
        level = int(data.get("level"))
        if level < MIN_LEVEL or level > MAX_LEVEL or level % 100 != 0:
            raise ValueError
    except (TypeError, ValueError):
        errors["level"] = f"Level must be between {MIN_LEVEL} and {MAX_LEVEL}."
    try:
        amount = _parse_amount(data.get("amount"))
    except (TypeError, ValueError, ArithmeticError):
        errors["amount"] = "Enter a valid positive amount."
    if errors:
        raise ValidationError(errors)

    fee, created = Fee.objects.update_or_create(
        department=dept_code,
        level=level,
        defaults={
            "amount": amount,
            "description": str(data.get("description") or "Departmental Dues").strip(),
            "is_active": bool(data.get("is_active", True)),
        },
    )
    return Response({"fee": _fee_json(fee)}, status=201 if created else 200)


@api_view(["PATCH", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_fee_detail(request, fee_id):
    try:
        fee = Fee.objects.get(pk=fee_id)
    except Fee.DoesNotExist:
        return Response({"detail": "Fee not found."}, status=404)

    if request.method == "DELETE":
        fee.delete()
        return Response(status=204)

    data = request.data or {}
    if "amount" in data:
        try:
            fee.amount = _parse_amount(data["amount"])
        except (TypeError, ValueError, ArithmeticError):
            raise ValidationError({"amount": "Enter a valid positive amount."})
    if "is_active" in data:
        fee.is_active = bool(data["is_active"])
    if "description" in data:
        fee.description = str(data["description"]).strip()[:200]
    fee.save()
    return Response({"fee": _fee_json(fee)})


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_receipt_verify(request):
    code = str(request.query_params.get("code", "")).strip()
    if not code:
        return Response({"valid": False, "reason": "Enter the Receipt No. or Reference printed on the PDF."})

    payment = None
    try:
        payment = Payment.objects.get(receipt_id=uuid.UUID(code))
    except (ValueError, Payment.DoesNotExist):
        payment = Payment.objects.filter(reference__iexact=code).first()

    if payment is None:
        return Response(
            {"valid": False, "reason": "No receipt found for that number. Treat as unverified until proven otherwise."}
        )
    if payment.status != Payment.Status.SUCCESS:
        return Response(
            {
                "valid": False,
                "reason": f"Payment exists but is {payment.status} - not a valid paid receipt.",
                "payment": _payment_summary(payment),
            }
        )
    return Response({"valid": True, "payment": _payment_summary(payment)})


def _session_payload(start_year, manual):
    return {
        "session": session_label(start_year),
        "session_start": start_year,
        "manual": manual,
    }


@api_view(["GET", "POST", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def admin_session(request):
    row = SessionConfig.objects.first()

    if request.method == "GET":
        start = row.session_start if row else session_start_year()
        return Response(_session_payload(start, bool(row)))

    if request.method == "DELETE":
        SessionConfig.objects.all().delete()
        return Response(_session_payload(SessionConfig.active_start(), False))

    try:
        year = int((request.data or {}).get("session_start"))
    except (TypeError, ValueError):
        raise ValidationError({"session_start": "Enter a session start year, e.g. 2026."})
    if year < 2000 or year > timezone.now().year + 1:
        raise ValidationError(
            {"session_start": f"Session start must be between 2000 and {timezone.now().year + 1}."}
        )
    SessionConfig.objects.update_or_create(pk=1, defaults={"session_start": year})
    return Response(_session_payload(year, True), status=201)

