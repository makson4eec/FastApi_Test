from datetime import UTC, datetime

import pytest

from app.models import Payment, Tariff

PAYMENT_FIELDS = {
    "id",
    "status",
    "tariff_id",
    "amount",
    "discount",
    "method",
    "installment_months",
    "schedule",
    "email",
    "created_at",
}


def test_tariffs(client, payment_count):
    response = client.get("/tariffs")
    assert response.status_code == 200
    tariffs = response.json()
    assert [{"title": t["title"], "price": t["price"]} for t in tariffs] == [
        {"title": "basic", "price": 990_000},
        {"title": "standard", "price": 1_990_000},
        {"title": "premium", "price": 2_990_000},
    ]
    assert all(set(t) == {"id", "title", "price"} for t in tariffs)
    assert len({t["id"] for t in tariffs}) == 3
    assert all(type(t["price"]) is int for t in tariffs)
    assert payment_count() == 0


@pytest.mark.parametrize("promo", [None, "KVITTO10", "kvitto10", "KvItTo10"])
@pytest.mark.parametrize("method", ["card", "sbp", "installment"])
@pytest.mark.parametrize("tariff_title", ["basic", "standard", "premium"])
def test_payment_amount_and_response(client, app, payment_data, promo, method, tariff_title):
    tariff = next(t for t in client.get("/tariffs").json() if t["title"] == tariff_title)
    payload = {**payment_data, "tariff_id": tariff["id"], "method": method, "promo_code": promo}
    if method == "installment":
        payload["installment_months"] = 3
    response = client.post("/payments", json=payload)
    assert response.status_code == 201
    payment = response.json()
    expected_discount = tariff["price"] // 10 if promo else 0
    assert set(payment) == PAYMENT_FIELDS
    assert payment["tariff_id"] == tariff["id"]
    assert payment["amount"] == tariff["price"] - expected_discount
    assert payment["discount"] == expected_discount
    assert payment["amount"] + payment["discount"] == tariff["price"]
    assert type(payment["amount"]) is type(payment["discount"]) is int
    assert payment["status"] == "pending"
    assert payment["method"] == method
    assert payment["email"] == payload["email"]
    created_at = datetime.fromisoformat(payment["created_at"])
    assert created_at.tzinfo == UTC
    assert abs((datetime.now(UTC) - created_at).total_seconds()) < 30
    if method == "installment":
        assert payment["installment_months"] == 3
        assert sum(payment["schedule"]) == payment["amount"]
    else:
        assert payment["schedule"] is None
        assert payment["installment_months"] is None
    assert client.get(f"/payments/{payment['id']}").json() == payment
    with app.state.session_factory() as session:
        stored = session.get(Payment, payment["id"])
        assert stored.amount == payment["amount"]
        assert stored.discount == expected_discount
        assert stored.status == "pending"


@pytest.mark.parametrize("months", [3, 6, 12])
@pytest.mark.parametrize("promo", [None, "kvitto10"])
def test_installment_api(client, payment_data, months, promo):
    response = client.post(
        "/payments",
        json={
            **payment_data,
            "method": "installment",
            "installment_months": months,
            "promo_code": promo,
        },
    )
    assert response.status_code == 201
    payment = response.json()
    schedule = payment["schedule"]
    assert payment["installment_months"] == months
    assert len(schedule) == months
    assert sum(schedule) == payment["amount"]
    assert schedule == sorted(schedule, reverse=True)
    assert max(schedule) - min(schedule) <= 1
    if months == 3 and promo is None:
        assert schedule == [663_334, 663_333, 663_333]


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "wrong-email"},
        {"method": "cash"},
        {"method": "installment"},
        {"method": "installment", "installment_months": None},
        *[
            {"method": "installment", "installment_months": m}
            for m in [0, -3, 4, 24, True, 3.0, "3"]
        ],
        {"installment_months": 3},
        {"method": "sbp", "installment_months": 6},
        {"promo_code": "OTHER"},
        {"promo_code": ""},
        {"promo_code": " KVITTO10 "},
        {"promo_code": 10},
        {"tariff_id": 0},
        {"tariff_id": -1},
        {"tariff_id": True},
        {"tariff_id": "1"},
        {"tariff_id": 1.0},
        {"tariff_id": 2**63},
        {"unexpected": 1},
    ],
)
def test_invalid_requests_do_not_create_payments(client, payment_data, payment_count, changes):
    response = client.post("/payments", json={**payment_data, **changes})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert payment_count() == 0


def test_missing_required_fields(client, payment_count):
    response = client.post("/payments", json={})
    assert response.status_code == 422
    assert {item["loc"][-1] for item in response.json()["detail"]} == {
        "tariff_id",
        "email",
        "method",
    }
    assert payment_count() == 0


def test_unknown_tariff(client, payment_data, payment_count):
    response = client.post("/payments", json={**payment_data, "tariff_id": 999_999})
    assert response.status_code == 404
    assert response.json() == {"detail": "Tariff not found"}
    assert payment_count() == 0


def test_missing_payment(client):
    response = client.get("/payments/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}


@pytest.mark.parametrize("payment_id", ["0", "-1", "abc", str(2**63)])
def test_invalid_path_id(client, payment_id):
    response = client.get(f"/payments/{payment_id}")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_replay(client, payment_data, payment_count):
    headers = {"Idempotency-Key": "order-123"}
    first = client.post("/payments", json=payment_data, headers=headers)
    second = client.post("/payments", json=payment_data, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    assert payment_count() == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "another@example.com"},
        {"method": "sbp"},
        {"tariff_id": 1},
        {"promo_code": "kvitto10"},
        {"method": "installment", "installment_months": 6},
    ],
)
def test_key_conflict(client, payment_data, payment_count, changes):
    headers = {"Idempotency-Key": "order-123"}
    original = client.post("/payments", json=payment_data, headers=headers).json()
    conflict = client.post("/payments", json={**payment_data, **changes}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency_conflict"}
    assert client.get(f"/payments/{original['id']}").json() == original
    assert payment_count() == 1


def test_replay_normalizes_optional_fields_and_promo(client, payment_data, payment_count):
    headers = {"Idempotency-Key": "normalized"}
    first = client.post(
        "/payments", json={**payment_data, "promo_code": "kvitto10"}, headers=headers
    )
    second = client.post(
        "/payments",
        json={
            **payment_data,
            "promo_code": "KVITTO10",
            "installment_months": None,
        },
        headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    assert payment_count() == 1


def test_without_key_creates_independent_payments(client, payment_data, payment_count):
    first = client.post("/payments", json=payment_data)
    second = client.post("/payments", json=payment_data)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert payment_count() == 2


@pytest.mark.parametrize("key", ["", "contains space", "x" * 256])
def test_invalid_idempotency_header(client, payment_data, payment_count, key):
    response = client.post("/payments", json=payment_data, headers={"Idempotency-Key": key})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert payment_count() == 0


def test_payment_filters(client, payment_data):
    one = client.post("/payments", json=payment_data).json()
    two = client.post("/payments", json={**payment_data, "email": "other@example.com"}).json()
    assert (
        client.post(
            "/webhooks/bank", json={"payment_id": one["id"], "status": "succeeded"}
        ).status_code
        == 200
    )
    all_payments = client.get("/payments")
    assert all_payments.status_code == 200
    assert [p["id"] for p in all_payments.json()] == [one["id"], two["id"]]
    assert [p["id"] for p in client.get("/payments", params={"status": "pending"}).json()] == [
        two["id"]
    ]
    filtered = client.get(
        "/payments", params={"email": payment_data["email"], "status": "succeeded"}
    )
    assert [p["id"] for p in filtered.json()] == [one["id"]]
    assert client.get("/payments", params={"email": "nobody@example.com"}).json() == []
    assert client.get("/payments", params={"email": "bad"}).status_code == 422
    assert client.get("/payments", params={"status": "other"}).status_code == 422


def test_discount_rounding_through_api(client, app, payment_data):
    with app.state.session_factory() as session, session.begin():
        tariff = Tariff(title="rounding-test", price=105)
        session.add(tariff)
        session.flush()
        tariff_id = tariff.id
    response = client.post(
        "/payments",
        json={
            **payment_data,
            "tariff_id": tariff_id,
            "promo_code": "kvitto10",
            "method": "installment",
            "installment_months": 3,
        },
    )
    assert response.status_code == 201
    payment = response.json()
    assert payment["discount"] == 11
    assert payment["amount"] == 94
    assert payment["schedule"] == [32, 31, 31]
