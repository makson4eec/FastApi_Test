import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import PROJECT_ROOT, initialize_database, make_engine
from app.main import create_app
from app.models import Base, Payment
from app.schemas import PaymentCreate
from app.services import create_payment


def test_restart_keeps_payment_and_does_not_duplicate_tariffs(database_url):
    settings = Settings(database_url=database_url)
    headers = {"Idempotency-Key": "persisted"}
    with TestClient(create_app(settings)) as first:
        tariffs = first.get("/tariffs").json()
        payload = {"tariff_id": tariffs[0]["id"], "email": "user@example.com", "method": "card"}
        created = first.post("/payments", json=payload, headers=headers)
        assert created.status_code == 201
        payment = created.json()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/tariffs").json() == tariffs
        read = restarted.get(f"/payments/{payment['id']}")
        assert read.status_code == 200
        assert read.json() == payment
        replay = restarted.post("/payments", json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == payment


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "unknown"},
        {"method": "cash"},
        {"amount": -1},
        {"discount": -1},
        {"method": "installment", "installment_months": None},
        {"method": "installment", "installment_months": 4},
        {"installment_months": 3},
        {"tariff_id": 99999},
        {"email": None},
        {"idempotency_key": "key", "request_hash": None},
    ],
)
def test_database_constraints_and_rollback(client, app, payment_data, payment_count, changes):
    with app.state.session_factory() as session:
        invalid = Payment(
            **{
                **payment_data,
                "amount": 1_990_000,
                "discount": 0,
                "status": "pending",
                **changes,
            }
        )
        with pytest.raises(IntegrityError), session.begin():
            session.add(invalid)
        assert session.scalar(select(Payment)) is None
    assert payment_count() == 0
    assert client.post("/payments", json=payment_data).status_code == 201


def test_unique_key_is_enforced_by_database(client, app, payment_data, payment_count):
    client.post("/payments", json=payment_data, headers={"Idempotency-Key": "unique"})
    with app.state.session_factory() as session:
        with pytest.raises(IntegrityError), session.begin():
            session.add(
                Payment(
                    **payment_data,
                    amount=1_990_000,
                    discount=0,
                    idempotency_key="unique",
                    request_hash="a" * 64,
                )
            )
    assert payment_count() == 1


def test_migration_matches_models_and_downgrade(database_url):
    engine = make_engine(database_url)
    try:
        initialize_database(engine)
        initialize_database(engine)
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0001"
            )
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
        assert "payments" not in inspect(engine).get_table_names()
        initialize_database(engine)
        assert {"payments", "tariffs"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_failed_insert_rolls_back_and_does_not_reserve_key(client, app, payment_data):
    with app.state.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_payment BEFORE INSERT ON payments "
            "BEGIN SELECT RAISE(ABORT, 'test insert failure'); END"
        )
    with app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            create_payment(session, PaymentCreate(**payment_data), "retry-after-error")
        assert not session.in_transaction()
        with session.begin():
            assert session.scalar(select(Payment)) is None
        with app.state.engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER reject_payment")
        payment, created = create_payment(
            session, PaymentCreate(**payment_data), "retry-after-error"
        )
        assert created is True
        assert payment.status == "pending"
        assert payment.idempotency_key == "retry-after-error"
