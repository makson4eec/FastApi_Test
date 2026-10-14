import pytest

from app.money import calculate_amount, installment_schedule


@pytest.mark.parametrize(
    ("price", "discount"),
    [(0, 0), (1, 0), (4, 0), (5, 1), (9, 1), (10, 1), (15, 2), (99, 10), (105, 11)],
)
def test_discount_rounding(price, discount):
    amount, actual_discount = calculate_amount(price, True)
    assert actual_discount == discount
    assert amount == price - discount
    assert amount + actual_discount == price
    assert type(amount) is type(actual_discount) is int
    assert calculate_amount(price, False) == (price, 0)


@pytest.mark.parametrize("months", [3, 6, 12])
def test_schedule_invariants_over_boundary_amounts(months):
    for amount in [*range(0, 151), 989_999, 990_000, 1_990_000, 2**63 - 1]:
        schedule = installment_schedule(amount, months)
        assert len(schedule) == months
        assert sum(schedule) == amount
        assert all(type(part) is int and part >= 0 for part in schedule)
        assert schedule == sorted(schedule, reverse=True)
        assert max(schedule) - min(schedule) <= 1
        assert schedule.count(amount // months + 1) == amount % months


def test_pdf_example():
    assert installment_schedule(1_990_000, 3) == [663_334, 663_333, 663_333]


@pytest.mark.parametrize(("amount", "months"), [(-1, 3), (100, 0), (100, 4)])
def test_invalid_calculation_arguments(amount, months):
    with pytest.raises(ValueError):
        installment_schedule(amount, months)
    with pytest.raises(ValueError):
        calculate_amount(-1, True)
