"""FastAPI application entry point for DengueGuard / AEDISIGHT."""

import os
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mangum import Mangum
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from src.backend.auth import router as auth_router
from src.backend.database import Base, engine
from src.backend.dependencies import get_current_admin_user, get_current_hospital_user
from src.backend.models import User
from src.backend.security import get_csrf_token, verify_csrf_token
from src.predict import generate_forecast

# --------------------------------------------------
# ENVIRONMENT / SESSION CONFIG
# --------------------------------------------------
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
if APP_ENV not in {"development", "test", "production"}:
    raise RuntimeError("APP_ENV must be development, test, or production.")

SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
if not SESSION_SECRET or SESSION_SECRET == "replace_with_a_random_secret" or len(SESSION_SECRET) < 32:
    raise RuntimeError("SESSION_SECRET is missing or too short (min 32 chars). Add it to your .env file.")

try:
    SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "3600"))
except ValueError as exc:
    raise RuntimeError("SESSION_MAX_AGE must be an integer.") from exc
if SESSION_MAX_AGE <= 0:
    raise RuntimeError("SESSION_MAX_AGE must be greater than zero.")

_cookie_raw = os.getenv("COOKIE_SECURE")
if _cookie_raw is None:
    COOKIE_SECURE = APP_ENV == "production"
else:
    _v = _cookie_raw.strip().lower()
    if _v not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise RuntimeError("COOKIE_SECURE must be a valid boolean value.")
    COOKIE_SECURE = _v in {"true", "1", "yes", "on"}
if APP_ENV == "production" and not COOKIE_SECURE:
    raise RuntimeError("COOKIE_SECURE must be true in production.")

# --------------------------------------------------
# APP
# --------------------------------------------------
app = FastAPI(title="DengueGuard", description="Urban Dengue Early Warning System", version="1.1.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="dengue_session",
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=COOKIE_SECURE,
)

app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
app.include_router(auth_router)


# --------------------------------------------------
# MODEL PREDICTION API
# --------------------------------------------------
class ForecastRequest(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    ward: str = Field(min_length=1, max_length=100)      # ward_id, e.g. "CCC_Ward_01"
    horizonWeeks: int = Field(ge=2, le=4)
    asOf: Optional[date] = None                          # forecast origin week; None = latest data


@app.post("/api/predict")
async def predict_ward(
    payload: ForecastRequest,
    request: Request,
    x_csrf_token: str = Header(default=""),
    current_user: User = Depends(get_current_hospital_user),
):
    """Authenticated ward forecast using the saved CatBoost model bundle."""
    if not verify_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token. Refresh the dashboard and try again.")
    if payload.horizonWeeks not in (2, 4):
        raise HTTPException(status_code=422, detail="Only 2-week and 4-week forecasts are supported.")
    as_of = payload.asOf.isoformat() if payload.asOf else None
    try:
        return generate_forecast(payload.city, payload.ward, payload.horizonWeeks, as_of)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Prediction failed. Check server logs and model/data compatibility.") from exc


# --------------------------------------------------
# PAGES
# --------------------------------------------------
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="home/index.html", context={})


@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="auth/login/login.html", context={"csrf_token": get_csrf_token(request)}
    )


@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="auth/register/register.html", context={"csrf_token": get_csrf_token(request)}
    )


@app.get("/user_dashboard")
async def user_dashboard(request: Request, current_user: User = Depends(get_current_hospital_user)):
    return templates.TemplateResponse(
        request=request,
        name="user/user_dashboard.html",
        context={"current_user": current_user, "csrf_token": get_csrf_token(request)},
    )


@app.get("/admin_dashboard")
async def admin_dashboard(request: Request, current_user: User = Depends(get_current_admin_user)):
    return templates.TemplateResponse(
        request=request,
        name="admin/admin_dashboard.html",
        context={"current_user": current_user, "csrf_token": get_csrf_token(request)},
    )


Base.metadata.create_all(bind=engine)

# Vercel ASGI handler
handler = Mangum(app)
