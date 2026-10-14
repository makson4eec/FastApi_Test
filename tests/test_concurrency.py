from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

import pytest
from sqlalchemy import event

from app.models import Payment


@pytest.mark.parametrize("explicit_transactions", [False, True])
def test_concurrent_duplicate_requests(
    client, app, payment_data, payment_count, explicit_transactions
):
    if explicit_transactions:
        # Also exercise transactional SELECTs instead of sqlite3's legacy mode.
        app.state.engine.dispose()

        @event.listens_for(app.state.engine, "connect")
        def explicit_connection(connection, _record):
            connection.isolation_level = None

        @event.listens_for(app.state.engine, "begin")
        def explicit_begin(connection):
            connection.exec_driver_sql("BEGIN")

    workers = 6
    read_barrier = Barrier(workers, timeout=10)
    thread_state = local()

    def synchronize_first_lookup(_conn, _cursor, statement, _parameters, _context, _many):
        if "WHERE payments.idempotency_key =" in statement and not getattr(
            thread_state, "seen", False
        ):
            thread_state.seen = True
            # Every request has executed its SELECT before any INSERT can start.
            read_barrier.wait()

    event.listen(app.state.engine, "after_cursor_execute", synchronize_first_lookup)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            responses = list(
                pool.map(
                    lambda _: client.post(
                        "/payments", json=payment_data, headers={"Idempotency-Key": "race"}
                    ),
                    range(workers),
                )
            )
    finally:
        event.remove(app.state.engine, "after_cursor_execute", synchronize_first_lookup)
    assert sorted(response.status_code for response in responses) == [200] * (workers - 1) + [201]
    assert all(response.json() == responses[0].json() for response in responses)
    assert payment_count() == 1


def test_concurrent_different_payloads(client, payment_data, payment_count):
    barrier = Barrier(2, timeout=10)

    def request(email):
        barrier.wait()
        return client.post(
            "/payments",
            json={**payment_data, "email": email},
            headers={"Idempotency-Key": "race-conflict"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, ["one@example.com", "two@example.com"]))
    assert sorted(response.status_code for response in responses) == [201, 409]
    created = next(response.json() for response in responses if response.status_code == 201)
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json() == {"error": "idempotency_conflict"}
    assert client.get(f"/payments/{created['id']}").json() == created
    assert payment_count() == 1


def test_concurrent_webhooks(client, app, payment_data):
    payment = client.post("/payments", json=payment_data).json()
    barrier = Barrier(2, timeout=10)

    def notify(status):
        barrier.wait()
        return status, client.post(
            "/webhooks/bank", json={"payment_id": payment["id"], "status": status}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(notify, ["succeeded", "failed"]))
    assert sorted(response.status_code for _, response in results) == [200, 409]
    winning_status = next(status for status, response in results if response.status_code == 200)
    assert next(response.json() for _, response in results if response.status_code == 409) == {
        "error": "invalid_transition"
    }
    with app.state.session_factory() as session:
        assert session.get(Payment, payment["id"]).status == winning_status
