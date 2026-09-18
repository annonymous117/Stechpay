import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase

from .matric import parse_matric
from .models import Department, Fee, Payment, SessionConfig
from .receipts import generate_receipt_pdf


class StechpayTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.session_config = SessionConfig.objects.create(session_start=2026)

        # Seed test departments including ICH and MCB
        self.csc = Department.objects.create(
            code="CSC", name="Computer Science", subaccount_code="ACCT_csc123", is_active=True
        )
        self.ich = Department.objects.create(
            code="ICH", name="Industrial Chemistry", subaccount_code="ACCT_ich456", is_active=True
        )
        self.mcb = Department.objects.create(
            code="MCB", name="Microbiology", subaccount_code="ACCT_mcb789", is_active=True
        )
        self.sta = Department.objects.create(
            code="STA", name="Statistics", subaccount_code="", is_active=True
        )

        # Seed fees
        self.fee_csc_100 = Fee.objects.create(department="CSC", level=100, amount=Decimal("25000.00"), is_active=True)
        self.fee_csc_200 = Fee.objects.create(department="CSC", level=200, amount=Decimal("22000.00"), is_active=True)
        self.fee_ich_300 = Fee.objects.create(department="ICH", level=300, amount=Decimal("20000.00"), is_active=True)
        self.fee_mcb_100 = Fee.objects.create(department="MCB", level=100, amount=Decimal("24000.00"), is_active=True)

        # Admin user
        self.admin = User.objects.create_superuser(username="testadmin", password="password123")

    def test_academic_level_calculation_fix(self):
        """Verify the academic level formula: (session_start - entry_year + 1) * 100."""
        # In session 2026/2027:
        # 2026 entrant is 100L
        res_26 = parse_matric("CSC/26/001")
        self.assertEqual(res_26["level"], 100)
        self.assertEqual(res_26["department_code"], "CSC")

        # 2025 entrant is 200L
        res_25 = parse_matric("CSC/25/002")
        self.assertEqual(res_25["level"], 200)

        # 2024 entrant is 300L
        res_24 = parse_matric("ICH/24/003")
        self.assertEqual(res_24["level"], 300)
        self.assertEqual(res_24["department_name"], "Industrial Chemistry")

        # 2023 entrant is 400L
        res_23 = parse_matric("MCB/23/004")
        self.assertEqual(res_23["level"], 400)
        self.assertEqual(res_23["department_name"], "Microbiology")

    @patch("userapp.paystack.initialize_transaction")
    def test_initiate_payment_with_subaccount(self, mock_init):
        """Test payment initiation attaches department subaccount."""
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/mock_ref",
            "reference": "STECH-TEST-REF",
        }

        resp = self.client.post(
            "/api/payments/initiate/",
            data=json.dumps({
                "full_name": "Tola Ade",
                "email": "tola@school.edu.ng",
                "matric_number": "CSC/26/001",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["already_paid"])

        mock_init.assert_called_once()
        _, kwargs = mock_init.call_args
        self.assertEqual(kwargs.get("subaccount"), "ACCT_csc123")

        payment = Payment.objects.get(matric_number="CSC/26/001")
        self.assertEqual(payment.subaccount_code, "ACCT_csc123")
        self.assertEqual(payment.amount, Decimal("25000.00"))
        self.assertEqual(payment.level, 100)

    @patch("userapp.paystack.initialize_transaction")
    @patch("userapp.paystack.verify_transaction")
    def test_verify_payment_flow(self, mock_verify, mock_init):
        """Test verifying transaction completes payment."""
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/mock",
            "reference": "STECH-TEST-VERIFY",
        }
        mock_verify.return_value = {
            "status": "success",
            "raw": {
                "amount": 2000000,
                "currency": "NGN",
                "channel": "card",
                "gateway_response": "Approved",
                "paid_at": "2026-09-04T10:00:00Z",
            },
        }

        resp = self.client.post(
            "/api/payments/initiate/",
            data=json.dumps({
                "full_name": "Ada Obi",
                "email": "ada@school.edu.ng",
                "matric_number": "ICH/24/010",
            }),
            content_type="application/json",
        )
        ref = resp.json()["reference"]

        v_resp = self.client.post(
            "/api/payments/verify/",
            data=json.dumps({"reference": ref}),
            content_type="application/json",
        )
        self.assertEqual(v_resp.status_code, 200)
        p = v_resp.json()["payment"]
        self.assertEqual(p["status"], "successful")
        self.assertEqual(p["department_code"], "ICH")
        self.assertEqual(p["level"], 300)
        self.assertIsNotNone(p["receipt_url"])

    def test_paystack_webhook_fulfillment_with_signature(self):
        """Test webhook with valid HMAC signature marks pending payment as successful."""
        payment = Payment.objects.create(
            full_name="Kola Bello",
            email="kola@school.edu.ng",
            matric_number="MCB/26/005",
            department_code="MCB",
            level=100,
            session_start=2026,
            amount=Decimal("24000.00"),
            reference="STECH-WEBHOOK-TEST-123",
        )

        webhook_payload = {
            "event": "charge.success",
            "data": {
                "reference": "STECH-WEBHOOK-TEST-123",
                "amount": 2400000,
                "currency": "NGN",
                "status": "success",
                "channel": "card",
                "gateway_response": "Successful",
            },
        }
        payload_bytes = json.dumps(webhook_payload).encode("utf-8")
        secret = getattr(settings, "PAYSTACK_SECRET_KEY", "") or "sk_test_mock"

        with patch.object(settings, "PAYSTACK_SECRET_KEY", secret):
            signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha512).hexdigest()
            resp = self.client.post(
                "/api/payments/webhook/",
                data=payload_bytes,
                content_type="application/json",
                HTTP_X_PAYSTACK_SIGNATURE=signature,
            )
            self.assertEqual(resp.status_code, 200)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertIsNotNone(payment.paid_at)

    def test_admin_permissions_enforced(self):
        """Verify unauthenticated requests cannot access admin stats or payments."""
        self.assertEqual(self.client.get("/api/admin/stats/").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/payments/").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/departments/").status_code, 403)

        # Login admin
        self.client.login(username="testadmin", password="password123")
        self.assertEqual(self.client.get("/api/admin/stats/").status_code, 200)
        self.assertEqual(self.client.get("/api/admin/departments/").status_code, 200)

    def test_admin_department_management(self):
        """Test admin can add and edit departments dynamically."""
        self.client.login(username="testadmin", password="password123")

        # Create new department
        resp = self.client.post(
            "/api/admin/departments/",
            data=json.dumps({
                "code": "BCH",
                "name": "Biochemistry",
                "subaccount_code": "ACCT_bch001",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        dept_id = resp.json()["department"]["id"]

        # Check meta view includes new department
        meta_resp = self.client.get("/api/meta/")
        codes = [d["code"] for d in meta_resp.json()["departments"]]
        self.assertIn("BCH", codes)

        # Patch department
        patch_resp = self.client.patch(
            f"/api/admin/departments/{dept_id}/",
            data=json.dumps({"name": "Applied Biochemistry"}),
            content_type="application/json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["department"]["name"], "Applied Biochemistry")

    def test_pdf_receipt_generation_with_qr(self):
        """Test receipt PDF builds with QR code."""
        payment = Payment.objects.create(
            full_name="Fatima Lawal",
            email="fatima@school.edu.ng",
            matric_number="CSC/25/099",
            department_code="CSC",
            level=200,
            session_start=2026,
            amount=Decimal("22000.00"),
            reference="STECH-PDF-TEST-001",
            status=Payment.Status.SUCCESS,
        )
        pdf_bytes = generate_receipt_pdf(payment)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 2000)

    @patch("userapp.paystack.initialize_transaction")
    def test_reusing_active_pending_payment(self, mock_init):
        """Test that initiating payment when a recent pending payment exists reuses it without duplicates."""
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/mock_pending_1",
            "reference": "STECH-PENDING-001",
        }
        payload = {
            "full_name": "Tola Ade",
            "email": "tola@school.edu.ng",
            "matric_number": "CSC/26/001",
        }
        # First call creates pending payment
        r1 = self.client.post("/api/payments/initiate/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(Payment.objects.filter(matric_number="CSC/26/001").count(), 1)

        # Second call in quick succession reuses the same pending payment
        r2 = self.client.post("/api/payments/initiate/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("resumed"))
        # Verify no duplicate payment records were created
        self.assertEqual(Payment.objects.filter(matric_number="CSC/26/001").count(), 1)
        self.assertEqual(mock_init.call_count, 1)

    @patch("userapp.paystack.initialize_transaction")
    def test_initiate_payment_handles_paystack_error(self, mock_init):
        """Test PaystackError gracefully returns 502 Bad Gateway instead of 500 crash."""
        from userapp.paystack import PaystackError
        mock_init.side_effect = PaystackError("Invalid Paystack credentials")

        payload = {
            "full_name": "Bola Tinubu",
            "email": "bola@school.edu.ng",
            "matric_number": "STA/26/005",
        }
        Fee.objects.create(department="STA", level=100, amount=Decimal("20000.00"), is_active=True)

        resp = self.client.post("/api/payments/initiate/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("Could not initiate transaction", resp.json()["detail"])

        payment = Payment.objects.filter(matric_number="STA/26/005").first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertIn("Gateway initialization failed", payment.gateway_response)

    def test_session_configuration_settings(self):
        """Test session cookie age is 10 minutes and session saves every request."""
        self.assertEqual(settings.SESSION_COOKIE_AGE, 600)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertTrue(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)

    def test_admin_login_rate_limiting(self):
        """Test admin login is throttled after exceeding the rate limit."""
        from django.core.cache import cache
        cache.clear()

        # Send 5 attempts (the allowed threshold)
        for _ in range(5):
            r = self.client.post(
                "/api/admin/login/",
                data=json.dumps({"username": "bad", "password": "bad"}),
                content_type="application/json",
            )
            self.assertEqual(r.status_code, 400)

        # 6th attempt should be throttled (429 Too Many Requests)
        r6 = self.client.post(
            "/api/admin/login/",
            data=json.dumps({"username": "bad", "password": "bad"}),
            content_type="application/json",
        )
        self.assertEqual(r6.status_code, 429)


