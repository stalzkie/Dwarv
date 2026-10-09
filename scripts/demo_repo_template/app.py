def apply_discount(price: float, discount_percent: float) -> float:
    """Apply a percentage discount to a price.

    >>> apply_discount(100.0, 20.0)
    80.0
    """
    return price - discount_percent
