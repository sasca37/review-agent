def calc_fee(amount):
    """이체 수수료: 100만원 이상 1000원, 미만 500원."""
    if amount >= 1_000_000:
        return 1000
    return 500
