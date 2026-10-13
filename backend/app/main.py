import hmac
import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import errors as pg

from .config import settings
from .db import pool
from .routers import (accounts, admin, auth, budgets, goals, insights, portfolio, reference, transactions)
from .services import ai

log = logging.getLogger("fintrack")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True, timeout=15)
    yield
    pool.close()


app = FastAPI(title="FinTrack API", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        cookie_token = request.cookies.get("fintrack_csrf", "")
        header_token = request.headers.get("X-CSRF-Token", "")
        if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
            return JSONResponse({"detail": "CSRF check failed. Refresh the page and try again."}, status_code=403)
    return await call_next(request)


for router in (auth.router, admin.users_router, admin.admin_router,
               reference.institutions, reference.account_types, reference.asset_classes,
               reference.securities, reference.prices, reference.categories, reference.rules,
               accounts.router, transactions.router, budgets.router, goals.router,
               portfolio.router, insights.router):
    app.include_router(router)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exc: psycopg.Error):
    """Turn database constraint errors into clean 4xx responses; never leak SQL details."""
    if isinstance(exc, (pg.RaiseException, pg.InvalidParameterValue)):
        return JSONResponse({"detail": exc.diag.message_primary}, status_code=400)
    if isinstance(exc, pg.UniqueViolation):
        if exc.diag.constraint_name == "uq_accounts_user_institution_type_name":
            return JSONResponse({"detail": "That account name and type already exist at this institution"}, status_code=409)
        if exc.diag.constraint_name == "uq_accounts_user_last4":
            return JSONResponse({"detail": "Those last four digits are already used on another account"}, status_code=409)
        return JSONResponse({"detail": "That already exists"}, status_code=409)
    if isinstance(exc, pg.ForeignKeyViolation):
        return JSONResponse({"detail": "Conflicts with related data (it is still in use, or a referenced item does not exist)"}, status_code=409)
    if isinstance(exc, (pg.CheckViolation, pg.NotNullViolation, pg.InvalidTextRepresentation,
                        pg.NumericValueOutOfRange, pg.StringDataRightTruncation, pg.InvalidDatetimeFormat)):
        return JSONResponse({"detail": "Invalid value"}, status_code=400)
    log.exception("Unhandled database error")
    return JSONResponse({"detail": "Database error"}, status_code=500)


@app.exception_handler(ai.AIUnavailable)
async def ai_unavailable(request: Request, exc: ai.AIUnavailable):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(ai.AIProviderError)
async def ai_provider_error(request: Request, exc: ai.AIProviderError):
    log.warning("Local AI service failed: %s", exc)
    return JSONResponse({"detail": "The local AI service failed. Try again shortly."}, status_code=502)


@app.exception_handler(ai.AIBadResponse)
async def ai_bad_response(request: Request, exc: ai.AIBadResponse):
    return JSONResponse({"detail": "The AI returned something unusable. Please try again."}, status_code=502)
