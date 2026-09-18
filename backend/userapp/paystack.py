import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    pass


def is_mock_mode():
    if getattr(settings, 'PAYSTACK_MOCK_MODE', False):
        return True
    key = getattr(settings, 'PAYSTACK_SECRET_KEY', '') or ''
    return not bool(key) or key.startswith('sk_test_placeholder') or key.lower() in ('mock', 'test_mock')


def initialize_transaction(email, amount, reference, callback_url, metadata=None, subaccount=None):
    """amount is in naira. Returns {'authorization_url', 'reference'}."""
    if is_mock_mode():
        logger.warning("PAYSTACK_SECRET_KEY not set - using mock checkout.")
        separator = "&" if "?" in callback_url else "?"
        return {
            "authorization_url": f"{callback_url}{separator}reference={reference}",
            "reference": reference,
        }

    payload_data = {
        "email": email,
        "amount": int(round(float(amount) * 100)),
        "currency": "NGN",
        "reference": reference,
        "callback_url": callback_url,
        "metadata": metadata or {},
    }
    if subaccount:
        payload_data["subaccount"] = subaccount
        # bearer "subaccount" or "account" (default)
        payload_data["bearer"] = "subaccount"

    try:
        response = requests.post(
            f"{BASE_URL}/transaction/initialize",
            headers={
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
            json=payload_data,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise PaystackError(f"Could not reach Paystack: {exc}") from exc

    payload = _json(response)
    if response.status_code != 200 or not payload.get("status"):
        raise PaystackError(payload.get("message", "Paystack initialization failed."))
    data = payload["data"]
    return {"authorization_url": data["authorization_url"], "reference": data["reference"]}



def verify_transaction(reference):
    """Returns {'status': 'success'|'failed'|'pending', 'raw': dict}."""
    if is_mock_mode():
        return {
            "status": "success",
            "raw": {"channel": "mock", "gateway_response": "Approved (mock mode)"},
        }

    try:
        response = requests.get(
            f"{BASE_URL}/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise PaystackError(f"Could not reach Paystack: {exc}") from exc

    payload = _json(response)
    if response.status_code != 200 or not payload.get("status"):
        raise PaystackError(payload.get("message", "Paystack verification failed."))

    data = payload["data"]
    gateway_status = str(data.get("status", "")).lower()
    status = "pending"
    if gateway_status == "success":
        status = "success"
    elif gateway_status in ("failed", "abandoned", "reversed"):
        status = "failed"
    return {"status": status, "raw": data}


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}
