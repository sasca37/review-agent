from bank.fee import calc_fee


def transfer(src, dst, amount):
    fee = calc_fee(amount)
    if src.balance < amount + fee:
        raise ValueError("잔액 부족")
    src.balance -= amount + fee
    dst.balance += amount
    return fee
