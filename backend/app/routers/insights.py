import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import db
from ..deps import current_user
from ..services.forecast import forecast_spending
from ..services.insights import INSIGHT_ENGINE, build_facts, generate_insights as build_insights

router = APIRouter(prefix="/api/insights", tags=["insights"])
MAX_GENERATIONS_PER_HOUR = 5       # protects the API budget from accidental loops / abuse


@router.get("/overview")
def overview(user: dict = Depends(current_user)):
    """Everything the dashboard needs in one round trip."""
    uid = user["id"]
    with db() as cur:
        cur.execute("SELECT bank_total, portfolio_value FROM v_net_worth WHERE user_id = %s", (uid,))
        nw = cur.fetchone()
        cur.execute(
            """SELECT month,
                      COALESCE(SUM(total) FILTER (WHERE kind = 'income'), 0)  AS income,
                      COALESCE(SUM(total) FILTER (WHERE kind = 'expense'), 0) AS expenses
               FROM v_monthly_category_spend
               WHERE user_id = %s AND month >= (date_trunc('month', current_date) - INTERVAL '5 months')::date
               GROUP BY month ORDER BY month""", (uid,))
        cashflow = cur.fetchall()
        cur.execute(
            """SELECT category, total FROM v_monthly_category_spend
               WHERE user_id = %s AND kind = 'expense' AND month = date_trunc('month', current_date)::date
               ORDER BY total DESC LIMIT 6""", (uid,))
        top = cur.fetchall()
    return {
        "bank_total": nw["bank_total"], "portfolio_value": nw["portfolio_value"],
        "net_worth": nw["bank_total"] + nw["portfolio_value"],
        "cashflow": cashflow, "top_categories_this_month": top,
    }


@router.get("/forecast")
def forecast(months: int = Query(3, ge=1, le=6), user: dict = Depends(current_user)):
    """Statistical forecast (no LLM involved): see services/forecast.py for the method."""
    with db() as cur:
        return forecast_spending(cur, user["id"], horizon=months)


@router.post("/generate", status_code=201)
def generate(user: dict = Depends(current_user)):
    """Collect aggregate facts -> build a deterministic summary -> save."""
    with db() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM ai_insights WHERE user_id = %s AND created_at > now() - INTERVAL '1 hour'",
            (user["id"],))
        if cur.fetchone()["n"] >= MAX_GENERATIONS_PER_HOUR:
            raise HTTPException(429, "Insight limit reached (5 per hour). Try again later.")
        facts = build_facts(cur, user["id"])

    content = build_insights(facts)

    with db() as cur:
        cur.execute(
            """INSERT INTO ai_insights (user_id, period_month, content, model)
               VALUES (%s, %s, %s::jsonb, %s) RETURNING id, period_month, content, model, created_at""",
            (user["id"], date.today().replace(day=1), json.dumps(content), INSIGHT_ENGINE))
        return cur.fetchone()


@router.get("/history")
def history(limit: int = Query(10, ge=1, le=50), user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(
            "SELECT id, period_month, content, model, created_at FROM ai_insights "
            "WHERE user_id = %s ORDER BY id DESC LIMIT %s", (user["id"], limit))
        return cur.fetchall()
