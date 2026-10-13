"""Authentication + role-based access control (RBAC) dependencies."""
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyCookie

from .db import db
from .security import decode_token

SESSION_COOKIE = "fintrack_session"
session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)


def current_user(token: str | None = Depends(session_cookie)) -> dict:
    """Validate the HttpOnly session cookie, then load the user fresh from the DB."""
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        user_id = int(decode_token(token)["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired session")

    with db() as cur:
        cur.execute(
            """SELECT u.id, u.email, u.full_name, u.is_active, r.name AS role
               FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s""",
            (user_id,),
        )
        user = cur.fetchone()
    if not user or not user["is_active"]:
        raise HTTPException(401, "Account not found or disabled")
    return user


def require_roles(*roles: str):
    def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, "You do not have permission to do that")
        return user
    return checker


staff_only = require_roles("admin", "staff")
admin_only = require_roles("admin")


def assert_owns_account(cur, account_id: int, user_id: int) -> None:
    cur.execute("SELECT 1 FROM accounts WHERE id = %s AND user_id = %s", (account_id, user_id))
    if not cur.fetchone():
        raise HTTPException(404, "Account not found")