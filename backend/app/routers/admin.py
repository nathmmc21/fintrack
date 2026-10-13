from fastapi import APIRouter, Depends, HTTPException, Response

from ..db import db, update_sql
from ..deps import admin_only, staff_only
from ..schemas import AdminUserIn, AdminUserUpdate
from ..security import hash_password

users_router = APIRouter(prefix="/api/users", tags=["users (admin)"], dependencies=[Depends(admin_only)])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

USER_SELECT = """SELECT u.id, u.email, u.full_name, u.is_active, u.created_at, r.name AS role
                 FROM users u JOIN roles r ON r.id = u.role_id"""


@users_router.get("")
def list_users():
    with db() as cur:
        cur.execute(USER_SELECT + " ORDER BY u.id")
        return cur.fetchall()


@users_router.get("/{user_id}")
def get_user(user_id: int):
    with db() as cur:
        cur.execute(USER_SELECT + " WHERE u.id = %s", (user_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "User not found")
    return row


@users_router.post("", status_code=201)
def create_user(body: AdminUserIn):
    with db() as cur:
        cur.execute(
            """INSERT INTO users (role_id, email, password_hash, full_name)
               VALUES ((SELECT id FROM roles WHERE name = %s), %s, %s, %s) RETURNING id""",
            (body.role, body.email.lower(), hash_password(body.password), body.full_name.strip()),
        )
        new_id = cur.fetchone()["id"]
        cur.execute(USER_SELECT + " WHERE u.id = %s", (new_id,))
        return cur.fetchone()


@users_router.put("/{user_id}")
def update_user(user_id: int, body: AdminUserUpdate, admin: dict = Depends(admin_only)):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Nothing to update")
    if user_id == admin["id"] and (data.get("role", "admin") != "admin" or data.get("is_active") is False):
        raise HTTPException(400, "You cannot demote or deactivate your own account")

    with db() as cur:
        if "role" in data:
            cur.execute("SELECT id FROM roles WHERE name = %s", (data.pop("role"),))
            data["role_id"] = cur.fetchone()["id"]
        if "password" in data:
            data["password_hash"] = hash_password(data.pop("password"))
        query, params = update_sql("users", data, "id = %s", (user_id,))
        cur.execute(query, params)
        if not cur.fetchone():
            raise HTTPException(404, "User not found")
        cur.execute(USER_SELECT + " WHERE u.id = %s", (user_id,))
        return cur.fetchone()


@users_router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, admin: dict = Depends(admin_only)):
    """Hard delete: also removes that user's accounts, transactions, budgets and goals (ON DELETE CASCADE).
    Prefer deactivating (is_active = false) in normal use."""
    if user_id == admin["id"]:
        raise HTTPException(400, "You cannot delete your own account")
    with db() as cur:
        cur.execute("DELETE FROM users WHERE id = %s RETURNING id", (user_id,))
        if not cur.fetchone():
            raise HTTPException(404, "User not found")
    return Response(status_code=204)


@admin_router.get("/stats", dependencies=[Depends(staff_only)])
def platform_stats():
    """Counts only. Staff can see how the platform is used, never an individual's transactions."""
    with db() as cur:
        cur.execute(
            """SELECT
                 (SELECT COUNT(*) FROM users)                                              AS users,
                 (SELECT COUNT(*) FROM users u JOIN roles r ON r.id = u.role_id
                   WHERE r.name = 'customer')                                              AS customers,
                 (SELECT COUNT(*) FROM accounts)                                           AS accounts,
                 (SELECT COUNT(*) FROM transactions)                                       AS transactions,
                 (SELECT COUNT(*) FROM transactions WHERE category_source = 'ai')          AS ai_categorized,
                 (SELECT COUNT(*) FROM transactions WHERE category_source = 'rule')        AS rule_categorized,
                 (SELECT COUNT(*) FROM transactions WHERE category_source = 'default')     AS uncategorized,
                 (SELECT COUNT(*) FROM ai_insights)                                        AS insights_generated"""
        )
        return cur.fetchone()


@admin_router.get("/audit-log", dependencies=[Depends(admin_only)])
def audit_log(limit: int = 100):
    with db() as cur:
        cur.execute(
            "SELECT id, table_name, record_id, action, old_data, new_data, changed_at "
            "FROM audit_log ORDER BY id DESC LIMIT %s", (min(max(limit, 1), 500),))
        return cur.fetchall()
