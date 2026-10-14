from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.money import INSTALLMENT_TERMS

MAX_ID = 2**63 - 1
PositiveId = Annotated[int, Field(strict=True, gt=0, le=MAX_ID)]


class PaymentMethod(StrEnum):
    CARD = "card"
    SBP = "sbp"
    INSTALLMENT = "installment"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tariff_id: PositiveId
    email: EmailStr
    method: PaymentMethod
    installment_months: Annotated[int, Field(strict=True)] | None = None
    promo_code: str | None = None

    @field_validator("promo_code")
    @classmethod
    def validate_promo(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.upper() != "KVITTO10":
            raise ValueError("Unknown promo code")
        return "KVITTO10"

    @model_validator(mode="after")
    def validate_installment(self) -> Self:
        if self.method == PaymentMethod.INSTALLMENT:
            if self.installment_months not in INSTALLMENT_TERMS:
                raise ValueError("Installment requires installment_months: 3, 6 or 12")
        elif self.installment_months is not None:
            raise ValueError("installment_months is only allowed for installment")
        return self


class BankWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: PositiveId
    status: PaymentStatus


class TariffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    price: int


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: PaymentStatus
    tariff_id: int
    amount: int
    discount: int
    method: PaymentMethod
    installment_months: int | None
    schedule: list[int] | None
    email: EmailStr
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        # SQLite stores naive datetimes; this column always contains UTC.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
