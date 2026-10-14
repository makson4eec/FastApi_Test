"""Create tariffs and payments."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(50), nullable=False, unique=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.CheckConstraint("price > 0", name="ck_tariff_price"),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tariff_id", sa.Integer(), sa.ForeignKey("tariffs.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("installment_months", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_idempotency_key"),
        sa.CheckConstraint("amount >= 0 AND discount >= 0", name="ck_payment_money"),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'refunded')",
            name="ck_payment_status",
        ),
        sa.CheckConstraint("method IN ('card', 'sbp', 'installment')", name="ck_payment_method"),
        sa.CheckConstraint(
            "(method = 'installment' AND installment_months IS NOT NULL "
            "AND installment_months IN (3, 6, 12)) OR "
            "(method IN ('card', 'sbp') AND installment_months IS NULL)",
            name="ck_payment_installment",
        ),
        sa.CheckConstraint(
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND length(idempotency_key) BETWEEN 1 AND 255 "
            "AND request_hash IS NOT NULL AND length(request_hash) = 64)",
            name="ck_payment_idempotency",
        ),
    )
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index("ix_payments_email", "payments", ["email"])


def downgrade() -> None:
    op.drop_index("ix_payments_email", table_name="payments")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_table("payments")
    op.drop_table("tariffs")
