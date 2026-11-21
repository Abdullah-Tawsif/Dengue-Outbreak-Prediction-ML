
"""FastAPI application entry point for DengueGuard."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve paths independently of the terminal's current directory.
PROJECT_ROOT = Path(__file__).resolve().parent

# Load environment variables before importing database configuration.
load_dotenv(PROJECT_ROOT / ".env", override=False)

# Session configuration.
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()

if (
    not SESSION_SECRET
    or SESSION_SECRET == "replace_with_a_random_secret"
    or len(SESSION_SECRET) < 32
):
    raise RuntimeError(
        "SESSION_SECRET is missing or too short. "
        "Generate a secure secret and add it to your .env file."
    )

try:
    SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "3600"))
except ValueError as exc:
    raise RuntimeError("SESSION_MAX_AGE must be an integer.") from exc

if SESSION_MAX_AGE <= 0:
    raise RuntimeError("SESSION_MAX_AGE must be greater than zero.")

COOKIE_SECURE = (
    os.getenv("COOKIE_SECURE", "false").strip().lower()
    in {"true", "1", "yes", "on"}
)

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
# --------------------------------------------------
# APPLICATION ENVIRONMENT AND SESSION CONFIGURATION
# --------------------------------------------------

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()

if APP_ENV not in {"development", "test", "production"}:
    raise RuntimeError(
        "APP_ENV must be development, test, or production."
    )

SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()

if (
    not SESSION_SECRET
    or SESSION_SECRET == "replace_with_a_random_secret"
    or len(SESSION_SECRET) < 32
):
    raise RuntimeError(
        "SESSION_SECRET is missing or too short. "
        "Generate a secure secret and add it to your .env file."
    )

try:
    SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "3600"))
except ValueError as exc:
    raise RuntimeError("SESSION_MAX_AGE must be an integer.") from exc

if SESSION_MAX_AGE <= 0:
    raise RuntimeError("SESSION_MAX_AGE must be greater than zero.")

# Secure cookies default to enabled in production.
cookie_secure_raw = os.getenv("COOKIE_SECURE")

if cookie_secure_raw is None:
    COOKIE_SECURE = APP_ENV == "production"
else:
    cookie_secure_value = cookie_secure_raw.strip().lower()

    if cookie_secure_value not in {
        "true", "false", "1", "0", "yes", "no", "on", "off"
    }:
        raise RuntimeError(
            "COOKIE_SECURE must be a valid boolean value."
        )

    COOKIE_SECURE = cookie_secure_value in {
        "true", "1", "yes", "on"
    }

if APP_ENV == "production" and not COOKIE_SECURE:
    raise RuntimeError(
        "COOKIE_SECURE must be true in production. "
        "Production session cookies must only be sent over HTTPS."
    )

from mangum import Mangum
from src.backend.database import Base, engine
from src.backend.models import User
from src.backend.auth import router as auth_router
from src.backend.security import get_csrf_token

from src.backend.dependencies import (
    get_current_hospital_user,
    get_current_admin_user,
)


# --------------------------------------------------
# APPLICATION
# --------------------------------------------------

app = FastAPI(
    title="DengueGuard",
    description="Urban Dengue Early Warning System",
    version="1.0.0",
)


# --------------------------------------------------
# SESSION MIDDLEWARE
# --------------------------------------------------

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="dengue_session",
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=COOKIE_SECURE,
)


# --------------------------------------------------
# STATIC FILES AND TEMPLATES
# --------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory=str(PROJECT_ROOT / "static")),
    name="static",
)

templates = Jinja2Templates(
    directory=str(PROJECT_ROOT / "templates")
)


# --------------------------------------------------
# AUTHENTICATION ROUTER
# --------------------------------------------------

app.include_router(auth_router)


# --------------------------------------------------
# PUBLIC HOME PAGE
# --------------------------------------------------

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="home/index.html",
        context={},
    )


# --------------------------------------------------
# LOGIN PAGE
# --------------------------------------------------

@app.get("/login")
async def login_page(request: Request):

    csrf_token = get_csrf_token(request)

    return templates.TemplateResponse(
        request=request,
        name="auth/login/login.html",
        context={
            "csrf_token": csrf_token,
        },
    )


# --------------------------------------------------
# REGISTRATION PAGE
# --------------------------------------------------

@app.get("/register")
async def register_page(request: Request):

    csrf_token = get_csrf_token(request)

    return templates.TemplateResponse(
        request=request,
        name="auth/register/register.html",
        context={
            "csrf_token": csrf_token,
        },
    )


# --------------------------------------------------
# HOSPITAL USER DASHBOARD
# --------------------------------------------------

@app.get("/user_dashboard")
async def user_dashboard(
    request: Request,
    current_user: User = Depends(get_current_hospital_user),
):

    csrf_token = get_csrf_token(request)

    return templates.TemplateResponse(
        request=request,
        name="user/user_dashboard.html",
        context={
            "current_user": current_user,
            "csrf_token": csrf_token,
        },
    )


# --------------------------------------------------
# ADMIN DASHBOARD
# --------------------------------------------------

@app.get("/admin_dashboard")
async def admin_dashboard(
    request: Request,
    current_user: User = Depends(get_current_admin_user),
):

    csrf_token = get_csrf_token(request)

    return templates.TemplateResponse(
        request=request,
        name="admin/admin_dashboard.html",
        context={
            "current_user": current_user,
            "csrf_token": csrf_token,
        },
    )


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

Base.metadata.create_all(bind=engine)


# --------------------------------------------------
# VERCEL ASGI HANDLER
# --------------------------------------------------

handler = Mangum(app)
