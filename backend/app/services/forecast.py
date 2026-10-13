"""Spending forecast.

Deliberately NOT done by the LLM: models are unreliable at arithmetic, and a forecast the user
can't reproduce is hard to trust. This is plain statistics on complete past months:

  * >= 4 months of history  -> baseline = mean of the last 3 months, plus the fitted linear trend
                               damped by 50% (a straight line through 6 points over-extrapolates).
  * 2-3 months of history   -> flat average.
  * the +/- band is one standard deviation of the residuals; the total's band adds the
    categories' variances in quadrature (they are treated as independent).

The first forecast month is the CURRENT month (the last complete month is the previous one), so the
UI can compare "projected for this month" with "spent so far" and with budgets.
"""
import math
import statistics
from datetime import date


def add_months(d: date, n: int) -> date:
    year, month0 = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(year, month0 + 1, 1)


def _money(x: float) -> float:
    return round(x, 2)


def forecast_spending(cur, user_id: int, horizon: int = 3, history: int = 6) -> dict:
    this_month = date.today().replace(day=1)
    last_complete = add_months(this_month, -1)

    cur.execute(
        "SELECT MIN(month) AS first_month FROM v_monthly_category_spend WHERE user_id = %s AND kind = 'expense'",
        (user_id,),
    )
    first_month = cur.fetchone()["first_month"]
    if first_month is None or first_month > last_complete:
        return {"status": "insufficient_data", "months_available": 0}

    start = max(first_month, add_months(last_complete, -(history - 1)))
    months, m = [], start
    while m <= last_complete:
        months.append(m)
        m = add_months(m, 1)
    if len(months) < 2:
        return {"status": "insufficient_data", "months_available": len(months)}

    cur.execute(
        "SELECT category_id, category, month, total FROM v_monthly_category_spend "
        "WHERE user_id = %s AND kind = 'expense' AND month BETWEEN %s AND %s",
        (user_id, start, last_complete),
    )
    series: dict[int, dict] = {}
    for row in cur.fetchall():
        entry = series.setdefault(row["category_id"], {"name": row["category"], "by_month": {}})
        entry["by_month"][row["month"]] = float(row["total"])

    cur.execute(
        "SELECT category_id, total FROM v_monthly_category_spend "
        "WHERE user_id = %s AND kind = 'expense' AND month = %s", (user_id, this_month))
    mtd = {r["category_id"]: float(r["total"]) for r in cur.fetchall()}

    cur.execute(
        "SELECT category_id, limit_amount FROM budgets WHERE user_id = %s AND budget_month = %s",
        (user_id, this_month))
    limits = {r["category_id"]: float(r["limit_amount"]) for r in cur.fetchall()}

    n = len(months)
    method = "damped_trend" if n >= 4 else "average"
    categories = []
    total_fc = [0.0] * horizon
    total_hist = [0.0] * n
    total_var = 0.0

    for category_id, info in series.items():
        ys = [info["by_month"].get(mo, 0.0) for mo in months]      # months with no spend count as 0
        if not any(ys):
            continue
        if n >= 4:
            slope, intercept = statistics.linear_regression(range(n), ys)
            residuals = [y - (intercept + slope * x) for x, y in enumerate(ys)]
            sigma = statistics.pstdev(residuals)
            base = statistics.fmean(ys[-3:])
            fc = [max(0.0, base + 0.5 * slope * k) for k in range(1, horizon + 1)]
        else:
            sigma = statistics.pstdev(ys)
            fc = [statistics.fmean(ys)] * horizon

        total_var += sigma ** 2
        total_fc = [t + f for t, f in zip(total_fc, fc)]
        total_hist = [t + y for t, y in zip(total_hist, ys)]
        limit = limits.get(category_id)
        categories.append({
            "category_id": category_id,
            "category": info["name"],
            "history": [_money(y) for y in ys],
            "forecast": [_money(f) for f in fc],
            "low": [_money(max(0.0, f - sigma)) for f in fc],
            "high": [_money(f + sigma) for f in fc],
            "spent_so_far": _money(mtd.get(category_id, 0.0)),
            "projected_this_month": _money(max(fc[0], mtd.get(category_id, 0.0))),
            "budget_limit": limit,
            "over_budget_risk": bool(limit is not None and max(fc[0], mtd.get(category_id, 0.0)) > limit),
        })

    categories.sort(key=lambda c: c["forecast"][0], reverse=True)
    band = math.sqrt(total_var)
    spent_total = sum(mtd.values())
    return {
        "status": "ok",
        "method": method,
        "history_months": [d.isoformat() for d in months],
        "forecast_months": [add_months(this_month, k).isoformat() for k in range(horizon)],
        "categories": categories,
        "total": {
            "history": [_money(v) for v in total_hist],
            "forecast": [_money(v) for v in total_fc],
            "low": [_money(max(0.0, v - band)) for v in total_fc],
            "high": [_money(v + band) for v in total_fc],
        },
        "this_month": {
            "spent_so_far": _money(spent_total),
            "projected": _money(max(total_fc[0], spent_total)),
            "budget_total": _money(sum(limits.values())) if limits else None,
        },
    }
