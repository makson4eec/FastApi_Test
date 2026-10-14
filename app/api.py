import hashlib
import hmac
from collections.abc import Iterator, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.models import Payment, Tariff
from app.schemas import MAX_ID, BankWebhook, PaymentCreate, PaymentRead, PaymentStatus, TariffRead

router = APIRouter()


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]
PaymentId = Annotated[int, Path(gt=0, le=MAX_ID)]
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=255, pattern=r"^[!-~]+$")]


async def verify_signature(
    request: Request, x_signature: Annotated[str | None, Header()] = None
) -> None:
    secret = request.app.state.settings.webhook_secret
    if secret is None:
        return
    expected = hmac.new(secret.encode("utf-8"), await request.body(), hashlib.sha256).hexdigest()
    # Comparing ASCII bytes also handles arbitrary non-ASCII header values safely.
    supplied = (x_signature or "").encode("utf-8")
    if not hmac.compare_digest(expected.encode("ascii"), supplied):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def payment_error_response(_request: Request, error: services.PaymentError) -> JSONResponse:
    errors = {
        services.PaymentNotFound: (404, {"detail": "Payment not found"}),
        services.TariffNotFound: (404, {"detail": "Tariff not found"}),
        services.IdempotencyConflict: (409, {"error": "idempotency_conflict"}),
        services.InvalidTransition: (409, {"error": "invalid_transition"}),
    }
    status_code, body = errors[type(error)]
    return JSONResponse(status_code=status_code, content=body)


@router.get("/tariffs", response_model=list[TariffRead])
def list_tariffs(session: SessionDependency) -> Sequence[Tariff]:
    return session.scalars(select(Tariff).order_by(Tariff.id)).all()


@router.post("/payments", response_model=PaymentRead, status_code=201)
def post_payment(
    payload: PaymentCreate,
    response: Response,
    session: SessionDependency,
    idempotency_key: IdempotencyKey = None,
) -> Payment:
    payment, created = services.create_payment(session, payload, idempotency_key)
    response.status_code = 201 if created else 200
    return payment


@router.get("/payments", response_model=list[PaymentRead])
def list_payments(
    session: SessionDependency,
    email: Annotated[EmailStr | None, Query()] = None,
    status: Annotated[PaymentStatus | None, Query()] = None,
) -> Sequence[Payment]:
    statement = select(Payment).order_by(Payment.id)
    if email is not None:
        statement = statement.where(Payment.email == str(email))
    if status is not None:
        statement = statement.where(Payment.status == status.value)
    return session.scalars(statement).all()


@router.get("/payments/{id}", response_model=PaymentRead)
def read_payment(id: PaymentId, session: SessionDependency) -> Payment:
    return services.get_payment(session, id)


@router.post("/webhooks/bank", dependencies=[Depends(verify_signature)])
def bank_webhook(payload: BankWebhook, session: SessionDependency) -> dict[str, str]:
    services.change_status(session, payload.payment_id, payload.status)
    return {"result": "ok"}
