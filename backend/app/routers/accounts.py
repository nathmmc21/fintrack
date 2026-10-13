from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response

from ..db import db, update_sql
from ..deps import current_user
from ..schemas import AccountIn, AccountUpdate, TransferIn

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _fetch(cur, account_id: int, user_id: int):
    cur.execute("SELECT * FROM v_account_balances WHERE account_id = %s AND user_id = %s", (account_id, user_id))
    return cur.fetchone()


@router.get("")
def list_accounts(user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute("SELECT * FROM v_account_balances WHERE user_id = %s ORDER BY account_id", (user["id"],))
        return cur.fetchall()


@router.post("", status_code=201)
def create_account(body: AccountIn, user: dict = Depends(current_user)):
    currency = body.currency_code
    if currency == "PHP":
        rate = 1
    elif body.fx_rate_to_php is None:
        raise HTTPException(400, "Enter the current PHP value of one unit of this currency")
    else:
        rate = body.fx_rate_to_php
    with db() as cur:
        cur.execute("SELECT is_investment FROM account_types WHERE id = %s", (body.account_type_id,))
        account_type = cur.fetchone()
        if not account_type:
            raise HTTPException(400, "Choose a valid account type")
        if account_type["is_investment"] and currency != "PHP":
            raise HTTPException(400, "Investment accounts use PHP because portfolio prices are recorded in PHP")
        if currency != "PHP":
            cur.execute("""SELECT fx_rate_to_php FROM accounts
                           WHERE user_id = %s AND currency_code = %s ORDER BY account_id LIMIT 1""",
                        (user["id"], currency))
            existing_rate = cur.fetchone()
            if existing_rate and existing_rate["fx_rate_to_php"] != rate:
                raise HTTPException(400, f"You already have a {currency} account. Use its current rate, or update that currency's rate first.")
        cur.execute(
            """INSERT INTO accounts (user_id, institution_id, account_type_id, name, last4, opening_balance,
                                     currency_code, fx_rate_to_php)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (user["id"], body.institution_id, body.account_type_id, body.name.strip(), body.last4,
             body.opening_balance, currency, rate))
        return _fetch(cur, cur.fetchone()["id"], user["id"])


@router.post("/transfer", status_code=201)
def transfer(body: TransferIn, user: dict = Depends(current_user)):
    """Stored procedure sp_transfer_funds writes both legs atomically."""
    with db() as cur:
        cur.execute(
            "CALL sp_transfer_funds(%s, %s, %s, %s, %s, %s, %s)",
            (user["id"], body.from_account_id, body.to_account_id, body.from_amount, body.to_amount,
             body.txn_date or date.today(), body.note))
    return {"ok": True}


@router.get("/{account_id}")
def get_account(account_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        row = _fetch(cur, account_id, user["id"])
    if not row:
        raise HTTPException(404, "Account not found")
    return row


@router.put("/{account_id}")
def update_account(account_id: int, body: AccountUpdate, user: dict = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Nothing to update")
    with db() as cur:
        cur.execute("SELECT currency_code FROM accounts WHERE id = %s AND user_id = %s", (account_id, user["id"]))
        account = cur.fetchone()
        if not account:
            raise HTTPException(404, "Account not found")
        if account["currency_code"] == "PHP" and data.get("fx_rate_to_php", 1) != 1:
            raise HTTPException(400, "PHP accounts always use an exchange rate of 1")
        if "fx_rate_to_php" in data and account["currency_code"] != "PHP":
            rate = data.pop("fx_rate_to_php")
            cur.execute("UPDATE accounts SET fx_rate_to_php = %s WHERE user_id = %s AND currency_code = %s",
                        (rate, user["id"], account["currency_code"]))
            if not data:
                return _fetch(cur, account_id, user["id"])
        if "account_type_id" in data and account["currency_code"] != "PHP":
            cur.execute("SELECT is_investment FROM account_types WHERE id = %s", (data["account_type_id"],))
            account_type = cur.fetchone()
            if account_type and account_type["is_investment"]:
                raise HTTPException(400, "Investment accounts use PHP because portfolio prices are recorded in PHP")
        query, params = update_sql("accounts", data, "id = %s AND user_id = %s", (account_id, user["id"]))
        cur.execute(query, params)
        if not cur.fetchone():
            raise HTTPException(404, "Account not found")
        return _fetch(cur, account_id, user["id"])


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, user: dict = Depends(current_user)):
    """Also deletes the account's transactions and trades (ON DELETE CASCADE)."""
    with db() as cur:
        cur.execute("DELETE FROM accounts WHERE id = %s AND user_id = %s RETURNING id", (account_id, user["id"]))
        if not cur.fetchone():
            raise HTTPException(404, "Account not found")
    return Response(status_code=204)
