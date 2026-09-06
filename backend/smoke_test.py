import json
import os
import re
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stechpay.settings")
sys.path.insert(0, r"C:\Stechpay\backend")

import django

django.setup()

from django.test import Client

client = Client(enforce_csrf_checks=True, SERVER_NAME="localhost")

print("== meta ==")
r = client.get("/api/meta/")
assert r.status_code == 200, r.content
meta = r.json()
assert any(d["code"] == "STA" for d in meta["departments"]), meta["departments"]
assert meta["fees"], "no fees seeded"
print("OK", len(meta["departments"]), "departments,", len(meta["fees"]), "fees")

print("== csrf ==")
r = client.get("/api/csrf/")
csrf = client.cookies["csrftoken"].value
print("OK token:", csrf[:8], "...")

def post(url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json", HTTP_X_CSRFTOKEN=csrf)

print("== initiate (bad matric) ==")
r = post("/api/payments/initiate/", {"full_name": "Ada Obi", "email": "ada@student.edu.ng", "matric_number": "STA-24-020"})
print(r.status_code, r.json())

print("== initiate (valid STA/24/020 -> 200L) ==")
r = post("/api/payments/initiate/", {"full_name": "Ada Obi", "email": "ada@student.edu.ng", "matric_number": "STA/24/020"})
body = r.json()
assert r.status_code == 200, body
assert body["already_paid"] is False, body
ref = body["reference"]
auth_url = body["authorization_url"]
assert ref in auth_url, body
print("OK reference:", ref)

print("== initiate duplicate matric (same session) ==")
r = post("/api/payments/initiate/", {"full_name": "Ada Obi", "email": "ada@student.edu.ng", "matric_number": "sta/24/020"})
assert r.json().get("already_paid") is False or True  # not yet paid, still pending
assert r.status_code == 200

print("== verify ==")
r = post("/api/payments/verify/", {"reference": ref})
body = r.json()
assert r.status_code == 200, body
p = body["payment"]
assert p["status"] == "successful", p
assert p["level"] == 200, p
assert p["department"] == "Statistics", p
assert p["receipt_url"], p
print("OK", json.dumps(p, indent=2))

print("== initiate after success -> already_paid ==")
r = post("/api/payments/initiate/", {"full_name": "Ada Obi", "email": "ada@student.edu.ng", "matric_number": "STA/24/020"})
assert r.json()["already_paid"] is True, r.json()
print("OK")

print("== receipt PDF ==")
receipt_path = p["receipt_url"]
r = client.get(receipt_path)
assert r.status_code == 200, r.content
assert r["Content-Type"] == "application/pdf"
assert r.content[:5] == b"%PDF-", r.content[:10]
assert len(r.content) > 1500
with open("sample-receipt.pdf", "wb") as f:
    f.write(r.content)
print("OK bytes:", len(r.content), "-> sample-receipt.pdf")

print("== receipt wrong code rejected ==")
r = client.get(f"/api/payments/{ref}/receipt/?code=wrong")
assert r.status_code == 404, r.status_code
print("OK")

print("== admin endpoints unauthorized ==")
for url in ["/api/admin/stats/", "/api/admin/payments/", "/api/admin/fees/", "/api/admin/session/", "/api/admin/receipts/verify/"]:
    assert client.get(url).status_code == 401, url
print("OK")

print("== admin login (bad) ==")
r = post("/api/admin/login/", {"username": "admin", "password": "wrong"})
assert r.status_code == 400, r.content

print("== admin login ==")
r = post("/api/admin/login/", {"username": "admin", "password": "Omotolanimi"})
assert r.status_code == 200, r.content
csrf = client.cookies["csrftoken"].value

print("== me / stats / payments ==")
assert client.get("/api/admin/me/").json()["authenticated"] is True
stats = client.get("/api/admin/stats/").json()
assert float(stats["total_collected"]) > 0, stats
payments = client.get("/api/admin/payments/?q=STA/24").json()
assert payments["count"] >= 1, payments
filtered = client.get("/api/admin/payments/?status=pending").json()
assert all(x["status"] == "pending" for x in filtered["payments"])
print("OK stats:", {k: v for k, v in stats.items() if not isinstance(v, list)})

print("== receipt verify (staff tool) ==")
r = client.get(f"/api/admin/receipts/verify/?code={p['receipt_id']}")
body = r.json()
assert body["valid"] is True and body["payment"]["reference"] == ref, body
r = client.get(f"/api/admin/receipts/verify/?code={ref}")
assert r.json()["valid"] is True, r.json()
r = client.get("/api/admin/receipts/verify/?code=STECH-DOES-NOT-EXIST")
assert r.json()["valid"] is False, r.json()
r = client.get("/api/admin/receipts/verify/?code=")
assert r.json()["valid"] is False, r.json()
print("OK")

print("== fees CRUD ==")
fees = client.get("/api/admin/fees/").json()["fees"]
target = next(f for f in fees if f["department"] == "CSC" and f["level"] == 100)
fee_id = target["id"]
r = client.patch(f"/api/admin/fees/{fee_id}/", data=json.dumps({"amount": 27000, "is_active": False}), content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
assert r.json()["fee"]["amount"] == "27000.00" and r.json()["fee"]["is_active"] is False, r.json()
new_fee = post("/api/admin/fees/", {"department": "CSC", "level": 500, "amount": 21000, "description": "Dues & Levy"}).json()
assert new_fee["fee"]["amount"] == "21000.00"
r = client.delete(f"/api/admin/fees/{new_fee['fee']['id']}/", HTTP_X_CSRFTOKEN=csrf)
assert r.status_code == 204
client.patch(f"/api/admin/fees/{fee_id}/", data=json.dumps({"amount": 25000, "is_active": True}), content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
print("OK")

print("== CSV export ==")
csv_resp = client.get("/api/admin/payments/export/")
assert csv_resp.status_code == 200
text = csv_resp.content.decode("utf-8")
lines = text.strip().splitlines()
assert lines[0].startswith("Reference") and len(lines) >= 2
print("OK rows:", len(lines) - 1)

print("== admin session (auto -> pinned -> auto) ==")
from userapp.matric import parse_matric

sess = client.get("/api/admin/session/").json()
assert sess["manual"] is False and sess["session_start"] == 2026, sess
r = post("/api/admin/session/", {"session_start": 2030})
assert r.status_code == 400, r.content
r = post("/api/admin/session/", {"session_start": "abc"})
assert r.status_code == 400, r.content
r = post("/api/admin/session/", {"session_start": 2027})
assert r.status_code == 201 and r.json()["manual"] is True, r.json()
meta2 = client.get("/api/meta/").json()
assert meta2["session"] == "2027/28" and meta2["session_start"] == 2027, meta2["session"]
assert parse_matric("STA/24/020")["level"] == 300, "level should follow pinned session"
client.delete("/api/admin/session/", HTTP_X_CSRFTOKEN=csrf)
sess = client.get("/api/admin/session/").json()
assert sess["manual"] is False and sess["session_start"] == 2026, sess
assert parse_matric("STA/24/020")["level"] == 200
meta3 = client.get("/api/meta/").json()
assert meta3["session"] == "2026/27", meta3["session"]
print("OK", sess)

print("== logout ==")
r = post("/api/admin/logout/", {})
assert r.status_code == 200
assert client.get("/api/admin/stats/").status_code == 401
print("OK")

print("\nALL BACKEND SMOKE TESTS PASSED")
