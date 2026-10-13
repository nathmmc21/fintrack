import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from ..config import settings
from ..db import db
from ..deps import SESSION_COOKIE, current_user
from ..schemas import RegisterIn
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
CSRF_COOKIE = "fintrack_csrf"

# Hash checked when the email is unknown, so "no such user" and "wrong password" take similar time.
_DUMMY_HASH = hash_password("not-a-real-password")


@router.get("/csrf")
def csrf_token(request: Request, response: Response):
    """Issue the double-submit token used to protect cookie-authenticated writes."""
    token = request.cookies.get(CSRF_COOKIE)
    if not token:
        token = secrets.token_urlsafe(32)
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=False,
            secure=settings.auth_cookie_secure,
            samesite="lax",
            path="/",
            max_age=60 * 60 * 24,
        )
    return {"status": "ok"}

@router.post("/register", status_code=201)
def register(body: RegisterIn):
    """Public sign-up always creates a customer; staff/admin accounts are created by an admin."""
    with db() as cur:
        cur.execute(
            """INSERT INTO users (role_id, email, password_hash, full_name)
               VALUES ((SELECT id FROM roles WHERE name = 'customer'), %s, %s, %s)
               RETURNING id, email, full_name""",
            (body.email.lower(), hash_password(body.password), body.full_name.strip()),
        )
        return cur.fetchone()


@router.post("/login")
def login(response: Response, form: OAuth2PasswordRequestForm = Depends()):
    with db() as cur:
        cur.execute(
            """SELECT u.id, u.password_hash, u.is_active, r.name AS role
               FROM users u JOIN roles r ON r.id = u.role_id WHERE u.email = %s""",
            (form.username.lower(),),
        )
        user = cur.fetchone()
    ok = verify_password(form.password, user["password_hash"] if user else _DUMMY_HASH)
    if not user or not ok or not user["is_active"]:
        raise HTTPException(401, "Incorrect email or password")

    token = create_access_token(user["id"], user["role"])
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"status": "authenticated", "role": user["role"]}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.auth_cookie_secure,
        httponly=True, samesite="lax",
    )
    return {"status": "logged_out"}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user