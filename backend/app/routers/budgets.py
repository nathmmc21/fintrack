from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response

from ..db import db
from ..deps import current_user
from ..schemas import BudgetCopyIn, BudgetIn, BudgetUpdate

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("")
def list_budgets(month: date | None = None, user: dict = Depends(current_user)):
    """Budget vs. actual for one month (defaults to the current month)."""
    month = (month or date.today()).replace(day=1)
    with db() as cur:
        cur.execute(
            "SELECT * FROM v_budget_status WHERE user_id = %s AND budget_month = %s ORDER BY pct_used DESC, category",
            (user["id"], month))
        return cur.fetchall()


@router.post("", status_code=201)
def create_budget(body: BudgetIn, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(     # only expense categories the user may use are accepted
            """INSERT INTO budgets (user_id, category_id, budget_month, limit_amount)
               SELECT %s, c.id, %s, %s FROM categories c
               WHERE c.id = %s AND c.kind = 'expense' AND (c.user_id IS NULL OR c.user_id = %s)
               RETURNING id""",
            (user["id"], body.budget_month, body.limit_amount, body.category_id, user["id"]))
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "Pick one of your expense categories")
        cur.execute("SELECT * FROM v_budget_status WHERE budget_id = %s", (row["id"],))
        return cur.fetchone()


@router.post("/copy")
def copy_budgets(body: BudgetCopyIn, user: dict = Depends(current_user)):
    """Stored procedure sp_copy_budgets: copy one month's budgets into another."""
    with db() as cur:
        cur.execute("CALL sp_copy_budgets(%s, %s, %s, NULL::int)", (user["id"], body.from_month, body.to_month))
        return {"copied": next(iter(cur.fetchone().values()))}


@router.put("/{budget_id}")
def update_budget(budget_id: int, body: BudgetUpdate, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute("UPDATE budgets SET limit_amount = %s WHERE id = %s AND user_id = %s RETURNING id",
                    (body.limit_amount, budget_id, user["id"]))
        if not cur.fetchone():
            raise HTTPException(404, "Budget not found")
        cur.execute("SELECT * FROM v_budget_status WHERE budget_id = %s", (budget_id,))
        return cur.fetchone()


@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute("DELETE FROM budgets WHERE id = %s AND user_id = %s RETURNING id", (budget_id, user["id"]))
        if not cur.fetchone():
            raise HTTPException(404, "Budget not found")
    return Response(status_code=204)
