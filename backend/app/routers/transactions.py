import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..db import db, escape_like, update_sql
from ..deps import assert_owns_account, current_user
from ..schemas import ClassifyIn, ImportIn, TransactionIn, TransactionUpdate
from ..services.categorizer import categorize

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

SELECT_TXN = """
    SELECT t.id, t.account_id, a.name AS account_name, a.currency_code, t.fx_rate_to_php,
           ROUND(t.amount * t.fx_rate_to_php, 2) AS amount_php, t.category_id, c.name AS category,
           c.kind AS category_kind, t.txn_date, t.description, t.amount,
           t.category_source, t.ai_confidence
    FROM transactions t
    JOIN accounts   a ON a.id = t.account_id
    JOIN categories c ON c.id = t.category_id
"""


def _fetch(cur, txn_id: int, user_id: int):
    cur.execute(SELECT_TXN + " WHERE t.id = %s AND a.user_id = %s", (txn_id, user_id))
    return cur.fetchone()


@router.get("")
def list_transactions(
    account_id: int | None = None, category_id: int | None = None,
    date_from: date | None = None, date_to: date | None = None, search: str | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    user: dict = Depends(current_user),
):
    params = {
        "uid": user["id"], "account_id": account_id, "category_id": category_id,
        "date_from": date_from, "date_to": date_to,
        "pattern": f"%{escape_like(search.strip())}%" if search and search.strip() else None,
        "limit": limit, "offset": offset,
    }
    with db() as cur:     # fixed SQL text; optional filters are NULL-able bound parameters
        cur.execute(
        """SELECT t.id, t.account_id, a.name AS account_name, a.currency_code, t.fx_rate_to_php,
                      ROUND(t.amount * t.fx_rate_to_php, 2) AS amount_php, t.category_id, c.name AS category,
                      c.kind AS category_kind, t.txn_date, t.description, t.amount,
                      t.category_source, t.ai_confidence, COUNT(*) OVER () AS total_count
               FROM transactions t
               JOIN accounts   a ON a.id = t.account_id
               JOIN categories c ON c.id = t.category_id
               WHERE a.user_id = %(uid)s
                 AND (%(account_id)s::int  IS NULL OR t.account_id  = %(account_id)s)
                 AND (%(category_id)s::int IS NULL OR t.category_id = %(category_id)s)
                 AND (%(date_from)s::date  IS NULL OR t.txn_date >= %(date_from)s)
                 AND (%(date_to)s::date    IS NULL OR t.txn_date <= %(date_to)s)
                 AND (%(pattern)s::text    IS NULL OR t.description ILIKE %(pattern)s)
               ORDER BY t.txn_date DESC, t.id DESC
               LIMIT %(limit)s OFFSET %(offset)s""",
            params,
        )
        rows = cur.fetchall()
    total = rows[0]["total_count"] if rows else 0
    for r in rows:
        r.pop("total_count")
    return {"total": total, "items": rows}


@router.post("/classify")
def classify_preview(body: ClassifyIn, user: dict = Depends(current_user)):
    """Preview only: nothing is saved. Returns the suggested category for each raw line."""
    results, ai_status = categorize(user["id"], [
        {**r.model_dump(), "amount": r.amount * (r.fx_rate_to_php or 1)} for r in body.rows
    ])
    return {"ai_status": ai_status, "results": results}


@router.post("/import", status_code=201)
def import_transactions(body: ImportIn, user: dict = Depends(current_user)):
    """Bulk-insert via the stored procedure. Rows without a category are categorised first."""
    with db() as cur:
        cur.execute("SELECT currency_code, fx_rate_to_php FROM accounts WHERE id = %s AND user_id = %s AND is_active",
                    (body.account_id, user["id"]))
        account = cur.fetchone()
    if not account:
        raise HTTPException(400, "Choose one of your active accounts")
    rows = [r.model_dump() for r in body.rows]
    for row in rows:
        row["fx_rate_to_php"] = row["fx_rate_to_php"] or account["fx_rate_to_php"]
        if account["currency_code"] == "PHP":
            row["fx_rate_to_php"] = 1
    missing = [i for i, r in enumerate(rows) if r["category_id"] is None]
    ai_status = "not_needed"
    if missing:
        results, ai_status = categorize(user["id"], [
            {**rows[i], "amount": rows[i]["amount"] * rows[i]["fx_rate_to_php"]} for i in missing
        ])
        for i, res in zip(missing, results):
            rows[i].update(res)

    payload = [{
        "category_id": r["category_id"], "txn_date": r["txn_date"].isoformat(),
        "description": r["description"], "amount": str(r["amount"]),
        "fx_rate_to_php": str(r["fx_rate_to_php"]),
        "category_source": r["category_source"] or "manual", "ai_confidence": r["ai_confidence"],
    } for r in rows]

    with db() as cur:
        cur.execute("CALL sp_import_transactions(%s, %s, %s::jsonb, NULL::int)",
                    (user["id"], body.account_id, json.dumps(payload)))
        inserted = next(iter(cur.fetchone().values()))
    return {"inserted": inserted, "ai_status": ai_status}


@router.post("/auto-categorize")
def auto_categorize(user: dict = Depends(current_user)):
    """Re-run rules + AI over the user's still-uncategorised transactions (most recent 100)."""
    with db() as cur:
        cur.execute(
            """SELECT t.id, t.description, t.amount * t.fx_rate_to_php AS amount FROM transactions t
               JOIN accounts a ON a.id = t.account_id
               WHERE a.user_id = %s AND t.category_source = 'default'
               ORDER BY t.txn_date DESC LIMIT 100""", (user["id"],))
        rows = cur.fetchall()
    if not rows:
        return {"examined": 0, "updated": 0, "ai_status": "not_needed"}

    results, ai_status = categorize(user["id"], rows)          # slow network call: no DB connection held
    changes = [(r["category_id"], r["category_source"], r["ai_confidence"], row["id"], user["id"])
               for row, r in zip(rows, results) if r["category_source"] != "default"]
    with db() as cur:
        cur.executemany(
            """UPDATE transactions SET category_id = %s, category_source = %s, ai_confidence = %s
               WHERE id = %s AND category_source = 'default'
                 AND account_id IN (SELECT id FROM accounts WHERE user_id = %s)""", changes)
    return {"examined": len(rows), "updated": len(changes), "ai_status": ai_status}


@router.post("", status_code=201)
def create_transaction(body: TransactionIn, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute("SELECT currency_code, fx_rate_to_php FROM accounts WHERE id = %s AND user_id = %s AND is_active",
                    (body.account_id, user["id"]))
        account = cur.fetchone()
    if not account:
        raise HTTPException(400, "Choose one of your active accounts")
    rate = 1 if account["currency_code"] == "PHP" else (body.fx_rate_to_php or account["fx_rate_to_php"])
    if body.category_id is None:
        (res,), ai_status = categorize(user["id"], [{"description": body.description, "amount": body.amount * rate}])
    else:
        res, ai_status = {"category_id": body.category_id, "category_source": "manual", "ai_confidence": None}, "not_needed"
    with db() as cur:
        assert_owns_account(cur, body.account_id, user["id"])
        cur.execute(
            """INSERT INTO transactions (account_id, category_id, txn_date, description, amount,
                                        category_source, ai_confidence, fx_rate_to_php)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (body.account_id, res["category_id"], body.txn_date, body.description.strip(), body.amount,
             res["category_source"], res["ai_confidence"], rate))
        created = _fetch(cur, cur.fetchone()["id"], user["id"])
    return {**created, "ai_status": ai_status}


@router.get("/{txn_id}")
def get_transaction(txn_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        row = _fetch(cur, txn_id, user["id"])
    if not row:
        raise HTTPException(404, "Transaction not found")
    return row


@router.put("/{txn_id}")
def update_transaction(txn_id: int, body: TransactionUpdate, user: dict = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Nothing to update")
    if "category_id" in data:                      # a human changed it: this is now the source of truth
        data.update(category_source="manual", ai_confidence=None)
    with db() as cur:
        if "fx_rate_to_php" in data:
            cur.execute("""SELECT a.currency_code FROM transactions t JOIN accounts a ON a.id = t.account_id
                           WHERE t.id = %s AND a.user_id = %s""", (txn_id, user["id"]))
            account = cur.fetchone()
            if not account:
                raise HTTPException(404, "Transaction not found")
            if account["currency_code"] == "PHP" and data["fx_rate_to_php"] != 1:
                raise HTTPException(400, "PHP transactions always use an exchange rate of 1")
        query, params = update_sql(
            "transactions", data,
            "id = %s AND account_id IN (SELECT id FROM accounts WHERE user_id = %s)", (txn_id, user["id"]))
        cur.execute(query, params)
        if not cur.fetchone():
            raise HTTPException(404, "Transaction not found")
        return _fetch(cur, txn_id, user["id"])


@router.delete("/{txn_id}", status_code=204)
def delete_transaction(txn_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(
            "DELETE FROM transactions WHERE id = %s AND account_id IN (SELECT id FROM accounts WHERE user_id = %s) RETURNING id",
            (txn_id, user["id"]))
        if not cur.fetchone():
            raise HTTPException(404, "Transaction not found")
    return Response(status_code=204)
