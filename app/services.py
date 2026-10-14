import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models import Payment, Tariff
from app.money import calculate_amount
from app.schemas import PaymentCreate, PaymentStatus


class PaymentError(Exception):
    pass


class PaymentNotFound(PaymentError):
    pass


class TariffNotFound(PaymentError):
    pass


class IdempotencyConflict(PaymentError):
    pass


class InvalidTransition(PaymentError):
    pass


def request_fingerprint(payload: PaymentCreate) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_replay(payment: Payment, fingerprint: str) -> Payment:
    if payment.request_hash != fingerprint:
        raise IdempotencyConflict
    return payment


def create_payment(
    session: Session, payload: PaymentCreate, key: str | None
) -> tuple[Payment, bool]:
    fingerprint = request_fingerprint(payload)
    with session.begin():
        if key is not None:
            existing = session.scalar(select(Payment).where(Payment.idempotency_key == key))
            if existing is not None:
                return check_replay(existing, fingerprint), False

        tariff = session.get(Tariff, payload.tariff_id)
        if tariff is None:
            raise TariffNotFound
        amount, discount = calculate_amount(tariff.price, payload.promo_code is not None)

    # Release the read snapshot before writing: SQLite cannot always upgrade it
    # after a concurrent commit, even with a busy timeout and WAL enabled.
    with session.begin():
        statement = (
            insert(Payment)
            .values(
                tariff_id=payload.tariff_id,
                email=str(payload.email),
                method=payload.method.value,
                installment_months=payload.installment_months,
                amount=amount,
                discount=discount,
                idempotency_key=key,
                request_hash=fingerprint if key is not None else None,
            )
            # Only this unique conflict is ignored; other DB errors propagate.
            .on_conflict_do_nothing(index_elements=[Payment.idempotency_key])
            .returning(Payment.id)
        )
        payment_id = session.scalar(statement)
        if payment_id is None:
            existing = session.scalars(select(Payment).where(Payment.idempotency_key == key)).one()
            return check_replay(existing, fingerprint), False
        return get_payment(session, payment_id), True


def get_payment(session: Session, payment_id: int) -> Payment:
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise PaymentNotFound
    return payment


def change_status(session: Session, payment_id: int, status: PaymentStatus) -> None:
    predecessors = {
        PaymentStatus.SUCCEEDED: "pending",
        PaymentStatus.FAILED: "pending",
        PaymentStatus.REFUNDED: "succeeded",
    }
    with session.begin():
        predecessor = predecessors.get(status)
        result = session.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == predecessor)
            .values(status=status.value)
        )
        if result.rowcount == 0:
            get_payment(session, payment_id)
            raise InvalidTransition
