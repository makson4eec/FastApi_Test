from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.money import installment_schedule


class Base(DeclarativeBase):
    pass


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (CheckConstraint("price > 0", name="ck_tariff_price"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50), unique=True)
    price: Mapped[int] = mapped_column(Integer)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_idempotency_key"),
        CheckConstraint("amount >= 0 AND discount >= 0", name="ck_payment_money"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'refunded')",
            name="ck_payment_status",
        ),
        CheckConstraint("method IN ('card', 'sbp', 'installment')", name="ck_payment_method"),
        CheckConstraint(
            "(method = 'installment' AND installment_months IS NOT NULL "
            "AND installment_months IN (3, 6, 12)) OR "
            "(method IN ('card', 'sbp') AND installment_months IS NULL)",
            name="ck_payment_installment",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND length(idempotency_key) BETWEEN 1 AND 255 "
            "AND request_hash IS NOT NULL AND length(request_hash) = 64)",
            name="ck_payment_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    amount: Mapped[int] = mapped_column(Integer)
    discount: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(20))
    installment_months: Mapped[int | None]
    email: Mapped[str] = mapped_column(String(320), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_hash: Mapped[str | None] = mapped_column(String(64))

    @property
    def schedule(self) -> list[int] | None:
        if self.installment_months is None:
            return None
        return installment_schedule(self.amount, self.installment_months)
