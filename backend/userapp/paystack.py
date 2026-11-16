import hashlib
import hmac
import re
import time
from decimal import Decimal
from urllib.parse import urlparse

import requests
from django.conf import settings

BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    def __init__(self, message="Payment provider is temporarily unavailable.", definitive=False):
        super().__init__(message)
        self.definitive = definitive


def is_mock_mode():
    # Test payments use Paystack's real test API. No synthetic approvals exist.
    return False


def configured_mode():
    mode = settings.PAYSTACK_MODE
    key = settings.PAYSTACK_SECRET_KEY
    if settings.PAYSTACK_MOCK_MODE or mode not in ("test", "live") or not re.fullmatch(r"sk_" + mode + r"_[A-Za-z0-9]{20,}", key or ""):
        raise PaystackError("Payment credentials are missing or do not match the configured payment mode.")
    if mode == "live" and not (settings.ALLOW_LIVE_PAYMENTS and settings.APP_ENV == 'production' and not settings.DEBUG
                               and settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'):
        raise PaystackError("Live payments require an explicit production rollout with PostgreSQL.")
    return mode


def minor_units(amount):
    value = Decimal(str(amount)) * 100
    if not value.is_finite() or value <= 0 or value != value.to_integral_value():
        raise PaystackError("Invalid payment amount.")
    return int(value)


def _request(method, path, payload=None):
    configured_mode()
    try:
        response = requests.request(method, BASE_URL + path,
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            json=payload, timeout=(5, 20), allow_redirects=False)
    except requests.RequestException as exc:
        raise PaystackError() from exc
    try:
        envelope = response.json()
    except ValueError as exc:
        raise PaystackError("Invalid response from payment provider.") from exc
    if not isinstance(envelope, dict) or response.status_code != 200 or envelope.get("status") is not True:
        raise PaystackError(definitive=response.status_code in (400, 401, 403, 422))
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise PaystackError("Invalid response from payment provider.")
    return data


def initialize_transaction(email, amount, reference, callback_url, metadata=None, subaccount=None):
    payload = {"email": email, "amount": minor_units(amount), "currency": "NGN", "reference": reference,
               "callback_url": callback_url, "metadata": metadata or {}}
    if subaccount:
        payload.update(subaccount=subaccount, bearer="subaccount")
    data = _request("POST", "/transaction/initialize", payload)
    try:
        url = urlparse(str(data.get("authorization_url", "")))
        valid = url.scheme == "https" and url.hostname == "checkout.paystack.com" and not url.username and not url.password and url.port in (None, 443)
    except ValueError:
        valid = False
    if data.get("reference") != reference or not valid:
        raise PaystackError("Invalid checkout response from payment provider.")
    return {"authorization_url": data["authorization_url"], "reference": reference}


def verify_transaction(reference):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", reference):
        raise PaystackError("Invalid payment reference.")
    data = _request("GET", f"/transaction/verify/{reference}")
    if data.get("reference") != reference:
        raise PaystackError("Payment provider returned a different reference.")
    return {"status": data.get("status", "pending"), "raw": data}


def valid_webhook(body, headers):
    try:
        configured_mode()
    except PaystackError:
        return False
    if settings.PAYSTACK_WEBHOOK_SECRETS:
        # Never downgrade to the legacy header after configuring signing secrets.
        values = headers.get("Paystack-Signature", "").split(",")
        stamps = [v.strip()[2:] for v in values if v.strip().startswith("t=")]
        signatures = [v.strip()[3:].split(":", 1)[-1] for v in values if v.strip().startswith("v1=")]
        try:
            if len(stamps) != 1 or abs(time.time() - int(stamps[0])) > 300:
                return False
        except ValueError:
            return False
        signed = stamps[0].encode() + b"." + body
        return any(hmac.compare_digest(hmac.new(secret.encode(), signed, hashlib.sha512).hexdigest(), sig)
                   for secret in settings.PAYSTACK_WEBHOOK_SECRETS for sig in signatures if re.fullmatch(r"[a-f0-9]{128}", sig))
    signature = headers.get("x-paystack-signature", "")
    return bool(re.fullmatch(r"[a-f0-9]{128}", signature) and hmac.compare_digest(
        hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest(), signature))
