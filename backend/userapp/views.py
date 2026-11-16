import csv
import json
from datetime import timedelta
from django.core.mail import send_mail
from django.utils.crypto import salted_hmac
import hashlib
import hmac
import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db import transaction, IntegrityError
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
from .passwords import is_exposed_password
from .constants import (
    LEVEL_CHOICES,
    MAX_LEVEL,
    MIN_LEVEL,
    department_name,
    session_label,
    session_start_year,
)
from .matric import normalize_matric, parse_matric
from .models import Department, Fee, Payment, SessionConfig, GatewayEvent, LoginChallenge
from .payments import reserve_checkout, reconcile, apply_verification, initialization_failed, sanitized_event, process_event
from .security import AdminPermission, admin_verified, can_read_payment, owner_hash, set_owner_cookie, audit
from .serializers import CheckoutSerializer, VerificationSerializer, LoginSerializer, DepartmentSerializer, FeeSerializer, PaymentListSerializer, SessionSerializer
from .receipts import generate_receipt_pdf

CALLBACK_PATH = "/callback"


def _callback_url(request):
    return f"{settings.FRONTEND_URL.rstrip(chr(47))}{CALLBACK_PATH}"


def _payment_summary(payment):
    return {
        "reference": payment.reference,
        "mode": payment.mode,
        "allocation": payment.allocation,
        "email_sent": payment.email_sent,
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
            f"/api/payments/{payment.reference}/receipt/"
            if payment.receipt_available
            else None
        ),
    }


def _fulfill_payment(payment, outcome_raw):
    updated = apply_verification(payment, outcome_raw)
    return updated.receipt_available, updated


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def csrf_view(request):
    response = Response({"detail": "CSRF cookie set."})
    set_owner_cookie(request, response)
    return response


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
            "paystack_mock_mode": False,
            "payment_mode": settings.PAYSTACK_MODE,
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
    serializer = CheckoutSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = dict(serializer.validated_data)
    data["email"] = data["email"].lower()
    parsed = parse_matric(data["matric_number"])
    data["matric_number"] = parsed["normalized"]
    owner = owner_hash(request)
    if not owner:
        return Response({"detail": "Refresh this page to start a secure checkout."}, status=403)
    try:
        key = str(uuid.UUID(request.headers.get("Idempotency-Key", "")))
    except ValueError:
        raise ValidationError({"detail": "A valid Idempotency-Key header is required."})
    fee = _get_fee_or_error(parsed["department_code"], parsed["level"])
    dept = Department.objects.get(code=parsed["department_code"], is_active=True)
    try:
        payment, created = reserve_checkout(data, parsed, fee, SessionConfig.active_start(), owner, key, dept.subaccount_code.strip())
        if not created:
            if payment.status != Payment.Status.PENDING:
                return Response({"already_paid": payment.receipt_available, "payment": _payment_summary(payment)})
            if payment.authorization_url:
                return Response({"already_paid": False, "authorization_url": payment.authorization_url, "reference": payment.reference, "resumed": True})
            return Response({"detail": "Checkout is being confirmed. Retry shortly using the same request.", "reference": payment.reference}, status=202)
        try:
            result = paystack.initialize_transaction(
                email=payment.email, amount=payment.amount, reference=payment.reference,
                callback_url=_callback_url(request),
                metadata={"matric_number": payment.matric_number, "session_start": payment.session_start},
                subaccount=payment.subaccount_code or None)
        except paystack.PaystackError as exc:
            initialization_failed(payment, exc)
            return Response({"detail": "Could not initiate transaction. Retry with the same request; do not start another payment while its status is unknown.", "reference": payment.reference}, status=502)
        Payment.objects.filter(pk=payment.pk).update(authorization_url=result["authorization_url"])
        return Response({"already_paid": False, "authorization_url": result["authorization_url"], "reference": payment.reference})
    except paystack.PaystackError:
        return Response({"detail": "Payment service is not configured or is temporarily unavailable."}, status=503)
    except IntegrityError:
        return Response({"detail": "This request conflicts with an existing checkout. Retry the original payment details."}, status=409)


@api_view(["POST"])
@permission_classes([AllowAny])
def verify_payment(request):
    serializer = VerificationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payment = Payment.objects.filter(reference=serializer.validated_data["reference"]).first()
    if not payment or not can_read_payment(request, payment):
        return Response({"detail": "Payment not found in this browser. Contact the bursary for receipt recovery."}, status=404)
    try:
        payment = reconcile(payment)
    except paystack.PaystackError:
        return Response({"detail": "Verification is temporarily unavailable. Please check again; do not pay again."}, status=503)
    except IntegrityError:
        return Response({"detail": "This gateway transaction requires bursary review."}, status=409)
    return Response({"payment": _payment_summary(payment)})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def paystack_webhook(request):
    if not paystack.valid_webhook(request.body, request.headers):
        return Response({"detail": "Invalid signature"}, status=401)
    payload = request.data
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str) or len(payload["event"]) > 80:
        raise ValidationError("Invalid webhook event.")
    clean = sanitized_event(payload)
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    GatewayEvent.objects.get_or_create(digest=digest, defaults={"event_type": clean["event"], "payload": clean})
    # Acknowledge only after durable persistence. The worker handles validation and retries.
    return Response({"status": "event queued"})


@api_view(["GET"])
@permission_classes([AllowAny])
def receipt_pdf(request, reference):
    payment = Payment.objects.filter(reference=reference).first()
    if not payment or not can_read_payment(request, payment):
        return HttpResponse("Receipt not found.", status=404)
    if not payment.receipt_available:
        return HttpResponse("Receipt available only for confirmed, allocated payments.", status=403)
    response = HttpResponse(generate_receipt_pdf(payment), content_type="application/pdf")
    filename = f"receipt-{payment.matric_number.replace('/', '-')}-{payment.session_start}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([])
def admin_login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    if is_exposed_password(serializer.validated_data['password']):
        return Response({"detail": "This password must be reset by an administrator before sign-in."}, status=403)
    user = authenticate(request, **serializer.validated_data)
    if user is None or not user.is_active or not user.is_staff:
        return Response({"detail": "Invalid credentials or not an administrator."}, status=400)
    if settings.ADMIN_MFA_REQUIRED:
        if not user.email:
            return Response({"detail": "An administrator email must be configured before sign-in."}, status=403)
        code = f"{secrets.randbelow(1000000):06d}"
        challenge = LoginChallenge.objects.create(user=user, code_hash=salted_hmac("admin-otp", code).hexdigest(),
            auth_hash=user.get_session_auth_hash(), expires_at=timezone.now() + timedelta(minutes=5))
        try:
            send_mail("Stechpay sign-in code", f"Your sign-in code is {code}. It expires in five minutes.", settings.DEFAULT_FROM_EMAIL, [user.email])
        except Exception:
            challenge.used = True
            challenge.save(update_fields=["used"])
            return Response({"detail": "Could not deliver the sign-in code. Try again later."}, status=503)
        request.session["login_challenge"] = str(challenge.pk)
        return Response({"mfa_required": True})
    login(request, user)
    request.session["admin_mfa_user"] = user.pk
    audit(request, "admin.login", user.pk)
    return Response({"username": user.username, "is_staff": True})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([])
def admin_login_confirm(request):
    code = str(request.data.get("code", ""))
    try:
        challenge_id = uuid.UUID(request.session.get("login_challenge", ""))
    except ValueError:
        return Response({"detail": "Start sign-in again."}, status=400)
    with transaction.atomic():
        challenge = LoginChallenge.objects.select_for_update().filter(pk=challenge_id).first()
        if not challenge or challenge.used or challenge.expires_at <= timezone.now() or challenge.attempts >= 5:
            return Response({"detail": "Code expired. Start sign-in again."}, status=400)
        challenge.attempts += 1
        user = challenge.user
        valid = (len(code) == 6 and code.isdigit() and user.is_active and user.is_staff and
            secrets.compare_digest(challenge.auth_hash, user.get_session_auth_hash()) and
            secrets.compare_digest(challenge.code_hash, salted_hmac("admin-otp", code).hexdigest()))
        challenge.used = valid
        challenge.save(update_fields=["attempts", "used"])
        if not valid:
            return Response({"detail": "Invalid sign-in code."}, status=400)
    login(request, user)
    request.session.pop("login_challenge", None)
    request.session["admin_mfa_user"] = user.pk
    audit(request, "admin.login", user.pk)
    return Response({"username": user.username, "is_staff": True})


@api_view(["POST"])
@permission_classes([AllowAny])
def admin_logout(request):
    logout(request)
    return Response({"detail": "Logged out."})


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_me(request):
    if admin_verified(request):
        return Response({"authenticated": True, "username": request.user.username})
    return Response({"authenticated": False})


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AdminPermission])
def admin_stats(request):
    success_q = Q(status=Payment.Status.SUCCESS, allocation="applied", mode=settings.PAYSTACK_MODE)
    totals = Payment.objects.filter(mode=settings.PAYSTACK_MODE).aggregate(
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
@permission_classes([AdminPermission])
def admin_payments(request):
    qs = Payment.objects.filter(mode=settings.PAYSTACK_MODE)
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

    params = PaymentListSerializer(data=request.query_params)
    params.is_valid(raise_exception=True)
    limit = params.validated_data["limit"]
    payments = [dict(_payment_summary(p)) | {"created_at": p.created_at.isoformat()} for p in qs[:limit]]
    return Response({"count": qs.count(), "payments": payments})


def _csv_text(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or any(c in text for c in ("\t", "\r", "\n")) else text


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AdminPermission])
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
            "Mode",
            "Allocation",
            "Channel",
            "Subaccount",
            "Paid At",
        ]
    )
    for p in Payment.objects.filter(mode=settings.PAYSTACK_MODE).iterator():
        writer.writerow(
            [
                p.reference,
                p.receipt_id,
                _csv_text(p.full_name),
                _csv_text(p.email),
                p.matric_number,
                _csv_text(p.department_display),
                f"{p.level}L",
                f"{p.session_start}/{p.session_start + 1}",
                p.amount,
                p.status,
                p.mode,
                p.allocation,
                _csv_text(p.channel or "-"),
                _csv_text(p.subaccount_code or "-"),
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
@permission_classes([AdminPermission])
@transaction.atomic
def admin_departments(request):
    if request.method == "GET":
        Department.get_active_map()
        return Response({"departments": [_department_json(d) for d in Department.objects.all()]})
    serializer = DepartmentSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    values = dict(serializer.validated_data)
    code = values.pop("code")
    dept, created = Department.objects.get_or_create(code=code, defaults=values)
    if not created:
        raise ValidationError({"code": "Department already exists."})
    audit(request, "department.create", dept.pk, serializer.validated_data)
    return Response({"department": _department_json(dept)}, status=201)


@api_view(["PATCH", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([AdminPermission])
@transaction.atomic
def admin_department_detail(request, dept_id):
    dept = Department.objects.select_for_update().filter(pk=dept_id).first()
    if not dept:
        return Response({"detail": "Department not found."}, status=404)
    if request.method == "DELETE":
        audit(request, "department.delete", dept.pk, {"code": dept.code})
        dept.delete()
        return Response(status=204)
    if "code" in request.data:
        raise ValidationError({"code": "Department codes cannot be changed."})
    serializer = DepartmentSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    changes = {key: {"before": getattr(dept, key), "after": value} for key, value in serializer.validated_data.items()}
    for key, value in serializer.validated_data.items():
        setattr(dept, key, value)
    dept.save()
    audit(request, "department.update", dept.pk, changes)
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
@permission_classes([AdminPermission])
@transaction.atomic
def admin_fees(request):
    if request.method == "GET":
        return Response({"fees": [_fee_json(f) for f in Fee.objects.all()]})
    serializer = FeeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    values = dict(serializer.validated_data)
    department, level = values.pop("department"), int(values.pop("level"))
    if department not in Department.get_active_map():
        raise ValidationError({"department": "Choose an active department."})
    fee, created = Fee.objects.update_or_create(department=department, level=level, defaults=values)
    audit(request, "fee.create" if created else "fee.update", fee.pk,
          {key: str(value) for key, value in serializer.validated_data.items()})
    return Response({"fee": _fee_json(fee)}, status=201 if created else 200)


@api_view(["PATCH", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([AdminPermission])
@transaction.atomic
def admin_fee_detail(request, fee_id):
    fee = Fee.objects.select_for_update().filter(pk=fee_id).first()
    if not fee:
        return Response({"detail": "Fee not found."}, status=404)
    if request.method == "DELETE":
        audit(request, "fee.delete", fee.pk)
        fee.delete()
        return Response(status=204)
    if "department" in request.data or "level" in request.data:
        raise ValidationError("Fee department and level cannot be changed.")
    serializer = FeeSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    changes = {key: {"before": str(getattr(fee, key)), "after": str(value)} for key, value in serializer.validated_data.items()}
    for key, value in serializer.validated_data.items():
        setattr(fee, key, value)
    fee.save()
    audit(request, "fee.update", fee.pk, changes)
    return Response({"fee": _fee_json(fee)})


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AdminPermission])
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
    if not payment.receipt_available:
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
@permission_classes([AdminPermission])
@transaction.atomic
def admin_session(request):
    row = SessionConfig.objects.first()
    if request.method == "GET":
        return Response(_session_payload(SessionConfig.active_start(), bool(row)))
    if request.method == "DELETE":
        audit(request, "session.reset", 1)
        SessionConfig.objects.all().delete()
        return Response(_session_payload(SessionConfig.active_start(), False))
    serializer = SessionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    year = serializer.validated_data["session_start"]
    if year > timezone.now().year + 1:
        raise ValidationError({"session_start": "Session cannot start more than one year in the future."})
    SessionConfig.objects.update_or_create(pk=1, defaults={"session_start": year})
    audit(request, "session.update", 1, {"session_start": year})
    return Response(_session_payload(year, True), status=201)

