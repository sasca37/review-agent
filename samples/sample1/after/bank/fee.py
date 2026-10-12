def calc_fee(amount, channel="web"):
    """이체 수수료: 100만원 이상 1000원, 미만 500원. 모바일은 면제."""
    if channel == "mobile":
        return 0
    if amount >= 1_000_000:
        return 1000
    return 500
