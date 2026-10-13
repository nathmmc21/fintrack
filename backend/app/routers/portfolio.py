from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response

from ..db import db
from ..deps import current_user
from ..schemas import TradeIn

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/positions")
def positions(user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute("SELECT * FROM v_portfolio_positions WHERE user_id = %s ORDER BY market_value DESC", (user["id"],))
        return cur.fetchall()


@router.get("/summary")
def summary(user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(
            """SELECT asset_class, SUM(market_value) AS market_value, SUM(cost_basis) AS cost_basis,
                      SUM(unrealized_gain) AS unrealized_gain
               FROM v_portfolio_positions WHERE user_id = %s GROUP BY asset_class ORDER BY market_value DESC""",
            (user["id"],))
        by_class = cur.fetchall()
    return {
        "market_value": sum(r["market_value"] for r in by_class) if by_class else 0,
        "cost_basis": sum(r["cost_basis"] for r in by_class) if by_class else 0,
        "unrealized_gain": sum(r["unrealized_gain"] for r in by_class) if by_class else 0,
        "by_asset_class": by_class,
    }


@router.get("/trades")
def list_trades(user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(
            """SELECT t.id, t.account_id, a.name AS account_name, t.security_id, s.symbol, t.trade_type,
                      t.quantity, t.price, t.fee, t.trade_date
               FROM investment_trades t
               JOIN accounts a   ON a.id = t.account_id
               JOIN securities s ON s.id = t.security_id
               WHERE a.user_id = %s ORDER BY t.trade_date DESC, t.id DESC""", (user["id"],))
        return cur.fetchall()


@router.post("/trades", status_code=201)
def create_trade(body: TradeIn, user: dict = Depends(current_user)):
    """Trigger trg_trade_check rejects non-investment accounts and over-selling."""
    with db() as cur:
        cur.execute(
            """INSERT INTO investment_trades (account_id, security_id, trade_type, quantity, price, fee, trade_date)
               SELECT a.id, %s, %s, %s, %s, %s, %s FROM accounts a WHERE a.id = %s AND a.user_id = %s
               RETURNING id, account_id, security_id, trade_type, quantity, price, fee, trade_date""",
            (body.security_id, body.trade_type, body.quantity, body.price, body.fee,
             body.trade_date or date.today(), body.account_id, user["id"]))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Account not found")
    return row


@router.delete("/trades/{trade_id}", status_code=204)
def delete_trade(trade_id: int, user: dict = Depends(current_user)):
    with db() as cur:
        cur.execute(
            """DELETE FROM investment_trades
               WHERE id = %s AND account_id IN (SELECT id FROM accounts WHERE user_id = %s)
               RETURNING account_id, security_id""", (trade_id, user["id"]))
        gone = cur.fetchone()
        if not gone:
            raise HTTPException(404, "Trade not found")
        cur.execute(      # removing a buy must not leave a negative holding -> roll back if it would
            """SELECT COALESCE(SUM(CASE WHEN trade_type = 'buy' THEN quantity ELSE -quantity END), 0) AS held
               FROM investment_trades WHERE account_id = %s AND security_id = %s""",
            (gone["account_id"], gone["security_id"]))
        if cur.fetchone()["held"] < 0:
            raise HTTPException(409, "Cannot delete this buy: later sells depend on it")
    return Response(status_code=204)
