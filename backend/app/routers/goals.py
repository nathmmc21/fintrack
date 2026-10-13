from fastapi import APIRouter, Depends, HTTPException, Response

from ..db import db, update_sql
from ..deps import assert_owns_account, current_user
from ..schemas import ContributionIn, GoalIn, GoalUpdate

router = APIRouter(prefix="/api/goals", tags=["goals"])


def _fetch(cur, goal_id: int, user_id: int):
    cur.execute("SELECT * FROM v_goal_progress WHERE goal_id = %s AND user_id = %s", (goal_id, user_id))
    return cur.fetchone()


@router.get("")
def list_goals(user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(
            "SELECT * FROM v_goal_progress WHERE user_id = %s ORDER BY (status <> 'active'), target_date NULLS LAST, goal_id",
            (user["id"],))
        return cur.fetchall()


@router.post("", status_code=201)
def create_goal(body: GoalIn, user: dict = Depends(current_user)):
    with db() as cur:
        if body.account_id is not None:
            assert_owns_account(cur, body.account_id, user["id"])
        cur.execute(
            """INSERT INTO goals (user_id, account_id, name, target_amount, target_date)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user["id"], body.account_id, body.name.strip(), body.target_amount, body.target_date))
        return _fetch(cur, cur.fetchone()["id"], user["id"])


@router.get("/{goal_id}")
def get_goal(goal_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        row = _fetch(cur, goal_id, user["id"])
    if not row:
        raise HTTPException(404, "Goal not found")
    return row


@router.put("/{goal_id}")
def update_goal(goal_id: int, body: GoalUpdate, user: dict = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Nothing to update")
    with db() as cur:
        if data.get("account_id") is not None:
            assert_owns_account(cur, data["account_id"], user["id"])
        query, params = update_sql("goals", data, "id = %s AND user_id = %s", (goal_id, user["id"]))
        cur.execute(query, params)
        if not cur.fetchone():
            raise HTTPException(404, "Goal not found")
        return _fetch(cur, goal_id, user["id"])


@router.delete("/{goal_id}", status_code=204)
def delete_goal(goal_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute("DELETE FROM goals WHERE id = %s AND user_id = %s RETURNING id", (goal_id, user["id"]))
        if not cur.fetchone():
            raise HTTPException(404, "Goal not found")
    return Response(status_code=204)


@router.get("/{goal_id}/contributions")
def list_contributions(goal_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(
            """SELECT gc.id, gc.amount, gc.contributed_on, gc.note
               FROM goal_contributions gc JOIN goals g ON g.id = gc.goal_id
               WHERE gc.goal_id = %s AND g.user_id = %s ORDER BY gc.contributed_on DESC, gc.id DESC""",
            (goal_id, user["id"]))
        return cur.fetchall()


@router.post("/{goal_id}/contributions", status_code=201)
def contribute(goal_id: int, body: ContributionIn, user: dict = Depends(current_user)):
    """Stored procedure sp_contribute_to_goal (optionally debits a source account). A trigger marks the
    goal 'achieved' when the target is reached."""
    with db() as cur:
        cur.execute("CALL sp_contribute_to_goal(%s, %s, %s, %s, %s, %s)",
                    (user["id"], goal_id, body.amount, body.from_account_id, body.from_account_amount, body.note))
        return _fetch(cur, goal_id, user["id"])
