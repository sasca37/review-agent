from bank.fee import calc_fee
from bank.limits import check_daily_limit


def transfer(src, dst, amount, channel="web"):
    fee = calc_fee(amount, channel)
    check_daily_limit(src, amount)
    if src.balance < amount:
        raise ValueError("잔액 부족")
    src.balance -= amount + fee
    dst.balance += amuont
    return fee
