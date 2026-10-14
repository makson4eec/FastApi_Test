import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Payment

STATUS_PATHS = {
    "pending": [],
    "succeeded": ["succeeded"],
    "failed": ["failed"],
    "refunded": ["succeeded", "refunded"],
}
ALLOWED = {("pending", "succeeded"), ("pending", "failed"), ("succeeded", "refunded")}


@pytest.mark.parametrize("initial", list(STATUS_PATHS))
@pytest.mark.parametrize("target", list(STATUS_PATHS))
def test_all_status_transitions(client, app, payment_data, initial, target):
    payment = client.post("/payments", json=payment_data).json()
    for status in STATUS_PATHS[initial]:
        response = client.post(
            "/webhooks/bank", json={"payment_id": payment["id"], "status": status}
        )
        assert response.status_code == 200
        assert response.json() == {"result": "ok"}
    before = client.get(f"/payments/{payment['id']}").json()
    response = client.post("/webhooks/bank", json={"payment_id": payment["id"], "status": target})
    if (initial, target) in ALLOWED:
        assert response.status_code == 200
        assert response.json() == {"result": "ok"}
        expected = target
    else:
        assert response.status_code == 409
        assert response.json() == {"error": "invalid_transition"}
        assert client.get(f"/payments/{payment['id']}").json() == before
        expected = initial
    with app.state.session_factory() as session:
        assert session.get(Payment, payment["id"]).status == expected


def test_missing_payment_webhook(client):
    response = client.post("/webhooks/bank", json={"payment_id": 999999, "status": "succeeded"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}


@pytest.mark.parametrize(
    "payload",
    [
        {"payment_id": 1, "status": "unknown"},
        {"payment_id": 1},
        {"payment_id": True, "status": "succeeded"},
        {"payment_id": 2**63, "status": "succeeded"},
        {"payment_id": 1, "status": "succeeded", "extra": "value"},
    ],
)
def test_invalid_webhook(client, payment_data, payload):
    payment = client.post("/payments", json=payment_data).json()
    before = client.get(f"/payments/{payment['id']}").json()
    response = client.post("/webhooks/bank", json=payload)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert client.get(f"/payments/{payment['id']}").json() == before


def test_replay_after_webhook_returns_current_payment(client, payment_data):
    headers = {"Idempotency-Key": "current-state"}
    payment = client.post("/payments", json=payment_data, headers=headers).json()
    assert (
        client.post(
            "/webhooks/bank", json={"payment_id": payment["id"], "status": "succeeded"}
        ).status_code
        == 200
    )
    replay = client.post("/payments", json=payment_data, headers=headers)
    assert replay.status_code == 200
    assert replay.json() == client.get(f"/payments/{payment['id']}").json()
    assert replay.json()["status"] == "succeeded"


def test_hmac_uses_original_bytes(database_url):
    secret = "test-secret-only"
    app = create_app(Settings(database_url=database_url, webhook_secret=secret))
    with TestClient(app) as client:
        tariff = client.get("/tariffs").json()[0]
        payment = client.post(
            "/payments",
            json={
                "tariff_id": tariff["id"],
                "email": "user@example.com",
                "method": "sbp",
            },
        ).json()
        raw_body = f'{{ "status" : "succeeded", "payment_id" : {payment["id"]} }}\n'.encode()
        signature = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        compact_body = f'{{"payment_id":{payment["id"]},"status":"succeeded"}}'.encode()
        for headers, body in [
            ({}, raw_body),
            ({"X-Signature": "0" * 64}, raw_body),
            ({"X-Signature": signature}, compact_body),
        ]:
            response = client.post(
                "/webhooks/bank",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    **headers,
                },
            )
            assert response.status_code == 401
            assert response.json() == {"detail": "Invalid webhook signature"}
            with app.state.session_factory() as session:
                assert session.get(Payment, payment["id"]).status == "pending"
        response = client.post(
            "/webhooks/bank",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Signature": signature,
            },
        )
        assert response.status_code == 200
        assert response.json() == {"result": "ok"}
        assert client.get(f"/payments/{payment['id']}").json()["status"] == "succeeded"
