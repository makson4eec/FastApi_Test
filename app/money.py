INSTALLMENT_TERMS = (3, 6, 12)


def calculate_amount(price: int, has_promo: bool) -> tuple[int, int]:
    """Return (amount, discount) in kopecks; round a 10% discount half up."""
    if price < 0:
        raise ValueError("Price must be non-negative")
    discount = (price + 5) // 10 if has_promo else 0
    return price - discount, discount


def installment_schedule(amount: int, months: int) -> list[int]:
    if amount < 0 or months not in INSTALLMENT_TERMS:
        raise ValueError("Invalid amount or installment term")
    part, remainder = divmod(amount, months)
    return [part + (index < remainder) for index in range(months)]
