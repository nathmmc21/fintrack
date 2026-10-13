"""Build concise server-calculated insights from aggregate financial facts."""
import calendar
import math
from datetime import date

from .forecast import add_months, forecast_spending

INSIGHT_ENGINE = "server-analysis-v1"


def _f(x):
    return None if x is None else float(x)


def build_facts(cur, user_id: int) -> dict:
    today = date.today()
    this_month = today.replace(day=1)
    last_month = add_months(this_month, -1)

    cur.execute(
        """SELECT category,
                  COALESCE(SUM(total) FILTER (WHERE month = %(this)s), 0) AS this_month,
                  COALESCE(SUM(total) FILTER (WHERE month = %(last)s), 0) AS last_month
           FROM v_monthly_category_spend
           WHERE user_id = %(uid)s AND kind = 'expense' AND month IN (%(this)s, %(last)s)
           GROUP BY category ORDER BY 2 DESC LIMIT 10""",
        {"uid": user_id, "this": this_month, "last": last_month},
    )
    spending = [{"category": r["category"], "this_month": _f(r["this_month"]), "last_month": _f(r["last_month"])}
                for r in cur.fetchall()]

    cur.execute(
        """SELECT COALESCE(SUM(total) FILTER (WHERE kind = 'income'  AND month = %(this)s), 0) AS income_this,
                  COALESCE(SUM(total) FILTER (WHERE kind = 'expense' AND month = %(this)s), 0) AS spend_this,
                  COALESCE(SUM(total) FILTER (WHERE kind = 'income'  AND month = %(last)s), 0) AS income_last,
                  COALESCE(SUM(total) FILTER (WHERE kind = 'expense' AND month = %(last)s), 0) AS spend_last
           FROM v_monthly_category_spend WHERE user_id = %(uid)s""",
        {"uid": user_id, "this": this_month, "last": last_month},
    )
    t = cur.fetchone()

    cur.execute(
        """SELECT category, limit_amount, spent, pct_used FROM v_budget_status
           WHERE user_id = %s AND budget_month = %s ORDER BY pct_used DESC""",
        (user_id, this_month),
    )
    budgets = [{"category": r["category"], "limit": _f(r["limit_amount"]), "spent": _f(r["spent"]),
                "percent_used": _f(r["pct_used"])} for r in cur.fetchall()]

    cur.execute(
        """SELECT name, target_amount, saved_amount, pct_complete, target_date, required_monthly
           FROM v_goal_progress WHERE user_id = %s AND status = 'active' ORDER BY target_date NULLS LAST""",
        (user_id,),
    )
    goals = [{"name": r["name"], "target": _f(r["target_amount"]), "saved": _f(r["saved_amount"]),
              "percent_complete": _f(r["pct_complete"]),
              "target_date": r["target_date"].isoformat() if r["target_date"] else None,
              "needed_per_month": _f(r["required_monthly"])} for r in cur.fetchall()]

    cur.execute(
        """SELECT COALESCE(SUM(market_value), 0) AS value, COALESCE(SUM(unrealized_gain), 0) AS gain
           FROM v_portfolio_positions WHERE user_id = %s""", (user_id,))
    p = cur.fetchone()

    fc = forecast_spending(cur, user_id)
    forecast = None
    if fc["status"] == "ok":
        forecast = {
            "method": fc["method"],
            "months": fc["forecast_months"],
            "total_spending": fc["total"]["forecast"],
            "this_month_projected": fc["this_month"]["projected"],
            "categories_at_risk_of_exceeding_budget": [c["category"] for c in fc["categories"] if c["over_budget_risk"]],
            "top_categories": [{"category": c["category"], "next_months": c["forecast"]} for c in fc["categories"][:4]],
        }

    return {
        "today": today.isoformat(),
        "days_elapsed": today.day,
        "days_in_month": calendar.monthrange(today.year, today.month)[1],
        "income": {"this_month": _f(t["income_this"]), "last_month": _f(t["income_last"])},
        "total_spending": {"this_month": _f(t["spend_this"]), "last_month": _f(t["spend_last"])},
        "spending_by_category": spending,
        "budgets_this_month": budgets,
        "active_goals": goals,
        "portfolio": {"market_value": _f(p["value"]), "unrealized_gain": _f(p["gain"])},
        "spending_forecast": forecast,
    }


def generate_insights(facts: dict) -> dict:
    """Build a small set of distinct, actionable observations without model-generated figures."""
    insights: list[dict] = []
    seen_titles: set[str] = set()
    seen_signals: set[tuple[str, str]] = set()

    def add(title: str, detail: str, severity: str, category: str | None = None,
            signal: tuple[str, str] | None = None) -> None:
        if len(insights) >= 3 or title in seen_titles or (signal and signal in seen_signals):
            return
        seen_titles.add(title)
        if signal:
            seen_signals.add(signal)
        insights.append({
            "title": title[:120],
            "detail": detail[:500],
            "severity": severity,
            "category": category[:60] if category else None,
        })

    def number(value) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    forecast = facts.get("spending_forecast") or {}
    at_risk = [str(name).strip()[:60] for name in forecast.get("categories_at_risk_of_exceeding_budget", []) if str(name).strip()]
    risk_names = {name.casefold() for name in at_risk}

    elapsed = number(facts.get("days_elapsed")) or 0
    days_in_month = number(facts.get("days_in_month")) or 0
    expected_budget_use = (elapsed / days_in_month * 100) if days_in_month > 0 else None
    budgets = sorted(
        facts.get("budgets_this_month", []),
        key=lambda item: number(item.get("percent_used")) or 0,
        reverse=True,
    )
    budget_categories: set[str] = set()
    for budget in budgets:
        category = str(budget.get("category") or "Budget").strip()[:60]
        pct = number(budget.get("percent_used"))
        key = category.casefold()
        if not category or pct is None or key in budget_categories:
            continue
        budget_categories.add(key)
        is_forecast_risk = key in risk_names
        if pct >= 100:
            detail = f"{category} is already over its planned budget this month."
            if is_forecast_risk:
                detail += " The forecast also flags a possible further overrun."
            add(f"{category} budget", detail, "warning", category, ("budget", key))
        elif is_forecast_risk:
            add(
                f"{category} budget risk",
                f"Current spending trends put {category} at risk of exceeding its budget this month.",
                "warning", category, ("budget", key),
            )
        elif expected_budget_use is not None and pct >= expected_budget_use + 15:
            add(
                f"{category} budget pace",
                f"{category} has used more of its budget than the portion of the month that has passed.",
                "warning", category, ("budget", key),
            )
        elif pct >= 80:
            add(f"{category} budget", f"{category} is close to its planned budget limit.",
                "warning", category, ("budget", key))

    # Include forecast-only categories, but never repeat a category already covered by its budget.
    for category in at_risk:
        key = category.casefold()
        if key not in budget_categories:
            add(f"{category} budget risk", f"Current spending trends put {category} at risk of exceeding its budget.",
                "warning", category, ("budget", key))

    goals = sorted(
        facts.get("active_goals", []),
        key=lambda item: number(item.get("percent_complete")) or 0,
        reverse=True,
    )
    for goal in goals:
        name = str(goal.get("name") or "Savings goal").strip()[:60]
        pct = number(goal.get("percent_complete"))
        if not name or pct is None or pct < 90:
            continue
        add(f"{name} goal", f"{name} is {round(pct)}% funded and getting close to its target.",
            "positive", name, ("goal", name.casefold()))

    if not insights:
        add("No budget alerts right now", "No current budget warning or near-complete savings goal was detected.", "info")

    has_warning = any(item["severity"] == "warning" for item in insights)
    has_positive = any(item["severity"] == "positive" for item in insights)
    if has_warning:
        headline = "Budget items to review"
    elif has_positive:
        headline = "A savings goal is nearing its target"
    else:
        headline = "No budget alerts right now"
    return {"headline": headline, "insights": insights}
