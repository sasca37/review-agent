def check_daily_limit(account, amount):
    if account.used_today + amount > account.daily_limit:
        raise ValueError("일일 한도 초과")
