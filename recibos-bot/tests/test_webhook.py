import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.whatsapp import RecipientNotAllowed, WhatsAppClient, verify_signature

from .conftest import image_msg, payload


def sign(body: bytes, secret="segredo"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature():
    body = b'{"a":1}'
    assert verify_signature("segredo", body, sign(body))
    assert not verify_signature("segredo", body, sign(body, "outro"))
    assert not verify_signature("segredo", body, None)
    assert not verify_signature("segredo", body, "abc")


def test_get_verification(bot):
    client = TestClient(create_app(bot))
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verifica",
                                        "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "errado",
                                         "hub.challenge": "12345"})
    assert bad.status_code == 403


def test_post_requires_valid_signature(bot):
    client = TestClient(create_app(bot))
    body = json.dumps(payload(image_msg())).encode()
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign(body, "errado"),
                                                       "Content-Type": "application/json"})
    assert r.status_code == 401
    assert bot.db.recent() == []

    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign(body),
                                                       "Content-Type": "application/json"})
    assert r.status_code == 200
    assert len(bot.db.recent()) == 1  # background task roda antes do TestClient retornar


def test_real_client_refuses_other_recipients():
    wa = WhatsAppClient(token="t", phone_number_id="p", graph_base="https://example.invalid", owner_phone="5511987654321")
    try:
        wa.send_text("5511900000000", "oi")
    except RecipientNotAllowed:
        pass
    else:
        raise AssertionError("deveria bloquear")
