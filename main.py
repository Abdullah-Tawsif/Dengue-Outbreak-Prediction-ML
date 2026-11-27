
"""FastAPI application entry point for DengueGuard."""

import os
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import pandas as pd

# Resolve paths independently of the terminal's current directory.
PROJECT_ROOT = Path(__file__).resolve().parent

# Load environment variables before importing database configuration.
load_dotenv(PROJECT_ROOT / ".env", override=False)

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
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

from src.backend.database import Base, engine, get_db, SessionLocal
from src.backend.models import (
    AuditLog,
    CrewAvailability,
    HospitalCapacity,
    LiveObservation,
    LivePrediction,
    User,
    WardContext,
    WardBedCapacity,
)
from src.backend.auth import router as auth_router
from src.backend.security import get_csrf_token, verify_csrf_token

from src.backend.dependencies import (
    get_current_hospital_user,
    get_current_admin_user,
)
PREDICTION_BACKEND = os.getenv("PREDICTION_BACKEND", "reference_catboost").strip().lower()
if PREDICTION_BACKEND == "reference_catboost":
    from src.reference_catboost_predict import (
        ForecastUnavailableError, ModelUnavailableError, data_freshness,
        generate_forecast, get_available_horizons, get_data_through,
        get_supported_date_bounds, get_supported_areas, DATA_PATH,
    )
elif PREDICTION_BACKEND == "phase2":
    from src.predict import (
        ForecastUnavailableError, ModelUnavailableError, data_freshness,
        generate_forecast, get_available_horizons, get_data_through,
        get_supported_date_bounds, get_supported_areas, DATA_PATH,
    )
else:
    raise RuntimeError("PREDICTION_BACKEND must be 'reference_catboost' or 'phase2'.")
from src.feature_engineering import SUPPORTED_HORIZONS
from src.live_input_predict import (
    LiveInputError,
    LiveModelUnavailableError,
    get_live_areas,
    get_live_defaults,
    predict_live,
)
from src.operations import (
    allocate_regional_stock,
    calculate_supply,
    plan_crews,
    rank_priority,
    route_ward_overflow,
)

logger = logging.getLogger(__name__)


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


class ForecastRequest(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    ward: str = Field(min_length=1, max_length=100)
    horizonWeeks: int = Field(ge=2, le=4)
    asOf: Optional[date] = None
    strictEvaluation: bool = False


class LiveForecastRequest(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    ward: str = Field(min_length=1, max_length=100)
    observationDate: date
    horizonWeeks: int = Field(ge=2, le=4)
    avgTempC: float = Field()
    maxTempC: float = Field()
    minTempC: float = Field()
    rainfallMm: float = Field(ge=0)
    humidityPct: float = Field(ge=0, le=100)
    dengueCases: Optional[float] = Field(default=None, ge=0)


class HospitalCapacityRequest(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    hospitalName: str = Field(min_length=1, max_length=255)
    totalBeds: Optional[int] = Field(default=None, ge=0)
    occupiedBeds: Optional[int] = Field(default=None, ge=0)


class WardBedCapacityRequest(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    wardId: str = Field(min_length=1, max_length=100)
    totalBeds: int = Field(ge=0)
    occupiedBeds: int = Field(ge=0)


class CrewAvailabilityRequest(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    crewName: str = Field(min_length=1, max_length=255)
    availableUnits: Optional[int] = Field(default=None, ge=0)
    maxWardsPerWeek: int = Field(default=1, ge=1, le=20)


class CounterfactualRequest(BaseModel):
    predictedCases: float = Field(ge=0)
    additionalCrews: int = Field(default=0, ge=0)
    additionalBeds: int = Field(default=0, ge=0)
    additionalDiagnosticKits: float = Field(default=0, ge=0)
    estimatedCost: float = Field(default=0, ge=0)
    assumedBenefitPoints: float = Field(default=0, ge=0)


@app.post("/api/predict")
async def predict_ward(
    payload: ForecastRequest,
    request: Request,
    x_csrf_token: str = Header(default=""),
    current_user: User = Depends(get_current_hospital_user),
):
    """Serve an authenticated forecast from the selected trained model run."""
    request_id = uuid.uuid4().hex
    if not verify_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    if payload.horizonWeeks not in SUPPORTED_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported forecast horizon; choose one of {SUPPORTED_HORIZONS} weeks.",
        )
    try:
        result = generate_forecast(
            payload.city,
            payload.ward,
            payload.horizonWeeks,
            payload.asOf.isoformat() if payload.asOf else None,
            strict_evaluation=payload.strictEvaluation,
        )
        result["requestId"] = request_id
        logger.info(
            "forecast request_id=%s city=%s ward=%s horizon_weeks=%s origin=%s mode=%s "
            "model=%s experiment=%s features=%s expected_features=%s missing_features=%s "
            "raw_prediction=%s expected_prediction=%s rounded_prediction=%s freshness=%s",
            request_id,
            result.get("city"),
            result.get("wardId"),
            result.get("horizonWeeks"),
            result.get("forecastOrigin"),
            result.get("mode"),
            result.get("model"),
            result.get("experimentId"),
            result.get("modelInputFeatureCount"),
            result.get("expectedFeatureCount"),
            result.get("missingFeatureCount"),
            result.get("rawPredictedCases"),
            result.get("expectedPredictedCases"),
            result.get("predictedCases"),
            result.get("dataFreshness"),
        )
        return result
    except (FileNotFoundError, ForecastUnavailableError, ModelUnavailableError) as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc) if isinstance(exc, ForecastUnavailableError) else "A compatible forecasting model or required data is unavailable.",
        ) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "forecast request failed request_id=%s city=%s ward=%s horizon_weeks=%s origin=%s",
            request_id, payload.city, payload.ward, payload.horizonWeeks,
            payload.asOf.isoformat() if payload.asOf else None,
        )
        raise HTTPException(
            status_code=500,
            detail="Prediction failed because the model or processed data could not be used.",
        ) from exc


@app.get("/live-input")
async def live_input_page(
    request: Request,
    current_user: User = Depends(get_current_hospital_user),
):
    csrf_token = get_csrf_token(request)
    try:
        areas = get_live_areas()
    except LiveInputError as exc:
        areas = {}
        data_error = str(exc)
    else:
        data_error = None
    return templates.TemplateResponse(
        request=request,
        name="user/live_input.html",
        context={
            "current_user": current_user,
            "csrf_token": csrf_token,
            "areas": areas,
            "data_error": data_error,
        },
        headers={"Cache-Control": "no-store, private"},
    )


@app.get("/api/live-input/defaults")
async def live_input_defaults(
    city: str = Query(min_length=1, max_length=100),
    ward: str = Query(min_length=1, max_length=100),
    current_user: User = Depends(get_current_hospital_user),
    db: Session = Depends(get_db),
):
    try:
        result = get_live_defaults(city, ward)
        latest_live = (
            db.query(LiveObservation)
            .filter(
                LiveObservation.city == city,
                LiveObservation.ward_id == ward,
            )
            .order_by(LiveObservation.observation_date.desc())
            .first()
        )
        result["latestCaseDate"] = result["latestHistoricalDate"]
        if latest_live is not None:
            live_date = latest_live.observation_date
            if latest_live.reported_dengue_cases is not None:
                result["previousCases"] = float(latest_live.reported_dengue_cases)
                result["latestCaseDate"] = live_date.isoformat()
            if live_date.isoformat() > result["latestHistoricalDate"]:
                result["latestHistoricalDate"] = live_date.isoformat()
                next_date = pd.Timestamp(live_date) + pd.Timedelta(weeks=1)
                result["nextExpectedWeeklyDate"] = next_date.date().isoformat()
        context = (
            db.query(WardContext)
            .filter(
                WardContext.city == city,
                WardContext.ward_id == ward,
            )
            .first()
        )
        if context is not None:
            if context.population_density_sqkm is not None:
                result["populationDensity"] = float(context.population_density_sqkm)
            if context.water_logging_risk_index is not None:
                result["waterLoggingRisk"] = float(context.water_logging_risk_index)
            if context.sanitation_score is not None:
                result["sanitationScore"] = float(context.sanitation_score)
        return result
    except LiveInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not load live ward defaults")
        raise HTTPException(status_code=503, detail="Live ward context is unavailable.") from exc


@app.post("/api/live-predict")
async def live_predict(
    payload: LiveForecastRequest,
    request: Request,
    x_csrf_token: str = Header(default=""),
    current_user: User = Depends(get_current_hospital_user),
    db: Session = Depends(get_db),
):
    if not verify_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")

    existing = (
        db.query(LiveObservation)
        .filter(
            LiveObservation.city == payload.city,
            LiveObservation.ward_id == payload.ward,
            LiveObservation.observation_date == payload.observationDate,
        )
        .first()
    )
    extra_rows = (
        db.query(LiveObservation)
        .filter(
            LiveObservation.city == payload.city,
            LiveObservation.ward_id == payload.ward,
            LiveObservation.observation_date != payload.observationDate,
        )
        .order_by(LiveObservation.observation_date.asc())
        .all()
    )
    try:
        ward_defaults = get_live_defaults(payload.city, payload.ward)
    except LiveInputError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ward_context = (
        db.query(WardContext)
        .filter(
            WardContext.city == payload.city,
            WardContext.ward_id == payload.ward,
        )
        .first()
    )
    if ward_context is not None:
        if ward_context.population_density_sqkm is not None:
            ward_defaults["populationDensity"] = float(ward_context.population_density_sqkm)
        if ward_context.water_logging_risk_index is not None:
            ward_defaults["waterLoggingRisk"] = float(ward_context.water_logging_risk_index)
        if ward_context.sanitation_score is not None:
            ward_defaults["sanitationScore"] = float(ward_context.sanitation_score)
    additional = pd.DataFrame([
        {
            "date": pd.Timestamp(row.observation_date),
            "city": row.city,
            "ward_id": row.ward_id,
            "population_density_sqkm": ward_defaults["populationDensity"],
            "water_logging_risk_index": ward_defaults["waterLoggingRisk"],
            "sanitation_score": ward_defaults["sanitationScore"],
            "avg_temp_C": row.avg_temp_c,
            "max_temp_c": row.max_temp_c,
            "min_temp_c": row.min_temp_c,
            "rainfall_mm": row.rainfall_mm,
            "relative_humidity_pct": row.humidity_pct,
            "dengue_cases": (
                None
                if row.dengue_case_source == "context_only"
                else (
                    row.reported_dengue_cases
                    if row.reported_dengue_cases is not None
                    else row.dengue_cases
                )
            ),
        }
        for row in extra_rows
    ])

    try:
        result = predict_live(
            city=payload.city,
            ward_id=payload.ward,
            observation_date=payload.observationDate,
            horizon_weeks=payload.horizonWeeks,
            avg_temp_c=payload.avgTempC,
            max_temp_c=payload.maxTempC,
            min_temp_c=payload.minTempC,
            rainfall_mm=payload.rainfallMm,
            relative_humidity_pct=payload.humidityPct,
            additional_observations=additional,
            static_context=ward_defaults,
        )

        observation = existing or LiveObservation(
            city=payload.city,
            ward_id=payload.ward,
            observation_date=payload.observationDate,
        )
        observation.avg_temp_c = payload.avgTempC
        observation.max_temp_c = payload.maxTempC
        observation.min_temp_c = payload.minTempC
        observation.rainfall_mm = payload.rainfallMm
        observation.humidity_pct = payload.humidityPct
        observation.dengue_cases = (
            payload.dengueCases
            if payload.dengueCases is not None
            else ward_defaults["previousCases"]
        )
        observation.reported_dengue_cases = payload.dengueCases
        observation.dengue_case_source = (
            "reported" if payload.dengueCases is not None else "context_only"
        )
        observation.submitted_by_user_id = current_user.id
        if existing is None:
            db.add(observation)
        db.flush()
        prediction = (
            db.query(LivePrediction)
            .filter(
                LivePrediction.observation_id == observation.id,
                LivePrediction.horizon_weeks == payload.horizonWeeks,
            )
            .first()
        )
        if prediction is None:
            prediction = LivePrediction(
                observation_id=observation.id,
                city=payload.city,
                ward_id=payload.ward,
                observation_date=payload.observationDate,
                horizon_weeks=payload.horizonWeeks,
            )
            db.add(prediction)
        prediction.target_date = date.fromisoformat(result["targetDate"])
        prediction.predicted_cases = result["predictedCases"]
        prediction.raw_predicted_cases = result["rawPredictedCases"]
        prediction.risk_category = result["riskCategory"]
        prediction.model_name = result["model"]
        prediction.experiment_id = result["experimentId"]
        prediction.model_artifact = result["modelArtifact"]
        db.commit()
        result["requestId"] = uuid.uuid4().hex
        result["persisted"] = True
        result["observationUpdated"] = existing is not None
        return result
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A duplicate live observation was detected.") from exc
    except (LiveInputError, LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (LiveModelUnavailableError, FileNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        headers={"Cache-Control": "no-store, private"},
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
        headers={"Cache-Control": "no-store, private"},
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
    data_error = None
    model_horizons = get_available_horizons()
    data_through = None
    freshness = None
    date_bounds = {}
    try:
        areas = get_supported_areas()
        data_through = get_data_through()
        freshness = data_freshness()
        date_bounds = get_supported_date_bounds()
    except (FileNotFoundError, ValueError, ForecastUnavailableError):
        areas = {}
        data_error = "Processed ward data is unavailable or invalid. Check the server configuration."

    return templates.TemplateResponse(
        request=request,
        name="user/user_dashboard.html",
        context={
            "current_user": current_user,
            "csrf_token": csrf_token,
            "areas": areas,
            "data_error": data_error,
            "model_horizons": model_horizons,
            "data_through": data_through,
            "freshness": freshness,
            "date_bounds": date_bounds,
        },
        headers={"Cache-Control": "no-store, private"},
    )


# --------------------------------------------------
# ADMIN DASHBOARD
# --------------------------------------------------

@app.get("/admin_dashboard")
async def admin_dashboard(
    request: Request,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Render the admin dashboard using live account records from the database."""

    csrf_token = get_csrf_token(request)

    # Keep the dashboard statistics aligned with the same users table shown below.
    # Hospital accounts exclude administrators; administrator count includes both
    # active and inactive administrator accounts.
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_hospital_accounts = (
        db.query(func.count(User.id))
        .filter(User.is_admin.is_(False), User.is_active.is_(True))
        .scalar()
        or 0
    )
    administrator_accounts = (
        db.query(func.count(User.id))
        .filter(User.is_admin.is_(True))
        .scalar()
        or 0
    )
    inactive_accounts = (
        db.query(func.count(User.id))
        .filter(User.is_active.is_(False))
        .scalar()
        or 0
    )

    # Do not expose password hashes or other internal fields to the template.
    users = (
        db.query(User)
        .order_by(User.created_at.desc(), User.id.desc())
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/admin_dashboard.html",
        context={
            "current_user": current_user,
            "csrf_token": csrf_token,
            "total_users": total_users,
            "active_hospital_accounts": active_hospital_accounts,
            "administrator_accounts": administrator_accounts,
            "inactive_accounts": inactive_accounts,
            "users": users,
        },
        headers={"Cache-Control": "no-store, private"},
    )


# --------------------------------------------------
# ADMIN-ONLY JSON API
# --------------------------------------------------


def _user_payload(user: User) -> dict:
    """Return only fields intended for the administrator user table."""
    return {
        "id": user.id,
        "full_name": user.full_name,
        "hospital_name": user.hospital_name,
        "email": user.email,
        "is_admin": bool(user.is_admin),
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@app.get("/api/admin/dashboard/summary")
async def admin_dashboard_summary(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Return live account totals for the administrator dashboard."""
    return {
        "total_users": db.query(func.count(User.id)).scalar() or 0,
        "active_hospital_accounts": (
            db.query(func.count(User.id))
            .filter(User.is_admin.is_(False), User.is_active.is_(True))
            .scalar() or 0
        ),
        "administrator_accounts": (
            db.query(func.count(User.id))
            .filter(User.is_admin.is_(True))
            .scalar() or 0
        ),
        "inactive_accounts": (
            db.query(func.count(User.id))
            .filter(User.is_active.is_(False))
            .scalar() or 0
        ),
    }


@app.get("/api/admin/users")
async def admin_list_users(
    search: str = Query(default="", max_length=255),
    role: str = Query(default="", pattern="^(|admin|hospital)$"),
    status_filter: str = Query(default="", alias="status", pattern="^(|active|inactive)$"),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Search and filter user accounts without exposing password hashes."""
    query = db.query(User)
    term = search.strip().casefold()
    if term:
        pattern = f"%{term}%"
        query = query.filter(
            func.lower(User.full_name).like(pattern)
            | func.lower(User.hospital_name).like(pattern)
            | func.lower(User.email).like(pattern)
        )
    if role == "admin":
        query = query.filter(User.is_admin.is_(True))
    elif role == "hospital":
        query = query.filter(User.is_admin.is_(False))
    if status_filter == "active":
        query = query.filter(User.is_active.is_(True))
    elif status_filter == "inactive":
        query = query.filter(User.is_active.is_(False))

    total = query.count()
    records = (
        query.order_by(User.created_at.desc(), User.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "users": [_user_payload(user) for user in records],
    }


@app.get("/api/admin/activity")
async def admin_recent_activity(
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Return persisted, non-sensitive audit events newest first."""
    events = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "description": event.description,
                "timestamp": event.created_at.isoformat(),
            }
            for event in events
        ]
    }


@app.get("/api/admin/system-status")
async def admin_system_status(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Report observed database, dataset, and model-artifact availability."""
    try:
        db.execute(text("SELECT 1"))
        database_status = "available"
    except Exception:
        database_status = "unavailable"

    processed_status = "missing"
    data_through = None
    freshness_status = "unknown"
    if DATA_PATH.is_file():
        processed_status = "available"
        try:
            freshness = data_freshness()
            data_through = freshness.get("dataThrough")
            freshness_status = freshness.get("status", "unknown")
        except Exception:
            processed_status = "invalid"
            freshness_status = "invalid"

    try:
        available_horizons = get_available_horizons()
    except Exception:
        available_horizons = []

    return {
        "backend": "available",
        "database": database_status,
        "raw_dataset": "not_configured",
        "processed_dataset": processed_status,
        "data_through": data_through,
        "data_freshness": freshness_status,
        "model_artifacts": "available" if available_horizons else "unavailable",
        "available_horizons": available_horizons,
        "prediction_service": "available" if available_horizons else "unavailable",
        "application_version": app.version,
    }


@app.get("/api/operations/status")
async def operations_status(
    city: str = Query(default="", max_length=100),
    available_stock_percent: int = Query(default=100, alias="availableStockPercent", ge=30, le=100),
    total_spraying_crews: Optional[int] = Query(default=None, alias="totalSprayingCrews", ge=0, le=500),
    current_user: User = Depends(get_current_hospital_user),
    db: Session = Depends(get_db),
):
    """Return constrained resource allocations and same-city ward surge routing."""
    prediction_query = db.query(LivePrediction)
    if city:
        prediction_query = prediction_query.filter(LivePrediction.city == city)
    predictions = (
        prediction_query
        .order_by(LivePrediction.created_at.desc(), LivePrediction.id.desc())
        .all()
    )

    latest_by_ward = {}
    for prediction in predictions:
        key = (prediction.city, prediction.ward_id)
        if key not in latest_by_ward:
            latest_by_ward[key] = prediction

    ward_rows = []
    supplies_by_key = {}
    for (prediction_city, ward_id), prediction in latest_by_ward.items():
        context = (
            db.query(WardContext)
            .filter(
                WardContext.city == prediction_city,
                WardContext.ward_id == ward_id,
            )
            .first()
        )
        supply = calculate_supply(prediction.predicted_cases)
        density = float(context.population_density_sqkm or 0) if context else 0.0
        priority = rank_priority({
            "predictedCases": prediction.predicted_cases,
            "growth": 0.0,
            "populationDensity": density,
            "waterLoggingRisk": context.water_logging_risk_index if context else 0.0,
            "sanitationScore": context.sanitation_score if context else 1.0,
        })
        row = {
            "city": prediction_city,
            "wardId": ward_id,
            "predictedCases": prediction.predicted_cases,
            "riskCategory": prediction.risk_category,
            "populationDensity": density,
            "priorityScore": priority,
            **{
                key: supply[key]
                for key in (
                    "saline_bags_needed",
                    "diagnostic_kits_needed",
                    "hospital_beds_required",
                )
            },
        }
        ward_rows.append(row)
        supplies_by_key[(prediction_city, ward_id)] = {
            **row,
            "supply": supply,
        }

    capacity_query = db.query(WardBedCapacity)
    if city:
        capacity_query = capacity_query.filter(WardBedCapacity.city == city)
    capacities = {
        (record.city, record.ward_id): record
        for record in capacity_query.all()
    }
    for row in ward_rows:
        record = capacities.get((row["city"], row["wardId"]))
        row["totalBeds"] = record.total_beds if record else None
        row["occupiedBeds"] = record.occupied_beds if record else None
        row["availableBeds"] = (
            max(0, record.total_beds - record.occupied_beds)
            if record else None
        )
    predicted_keys = {(row["city"], row["wardId"]) for row in ward_rows}
    for (capacity_city, ward_id), record in capacities.items():
        if (capacity_city, ward_id) in predicted_keys:
            continue
        context = (
            db.query(WardContext)
            .filter(
                WardContext.city == capacity_city,
                WardContext.ward_id == ward_id,
            )
            .first()
        )
        density = float(context.population_density_sqkm or 0) if context else 0.0
        supply = calculate_supply(0)
        row = {
            "city": capacity_city,
            "wardId": ward_id,
            "predictedCases": 0.0,
            "riskCategory": "LOW",
            "populationDensity": density,
            "priorityScore": rank_priority({
                "predictedCases": 0,
                "populationDensity": density,
                "waterLoggingRisk": context.water_logging_risk_index if context else 0,
                "sanitationScore": context.sanitation_score if context else 1,
            }),
            "saline_bags_needed": supply["saline_bags_needed"],
            "diagnostic_kits_needed": supply["diagnostic_kits_needed"],
            "hospital_beds_required": supply["hospital_beds_required"],
            "totalBeds": record.total_beds,
            "occupiedBeds": record.occupied_beds,
            "availableBeds": max(0, record.total_beds - record.occupied_beds),
        }
        ward_rows.append(row)
        supplies_by_key[(capacity_city, ward_id)] = {**row, "supply": supply}

    stock_allocations = {}
    rows_by_city = {}
    for row in ward_rows:
        rows_by_city.setdefault(row["city"], []).append(row)
    for prediction_city, city_rows in rows_by_city.items():
        stock_allocations[prediction_city] = allocate_regional_stock(
            city_rows,
            available_stock_percent,
        )
        for allocation in stock_allocations[prediction_city]["wardAllocations"]:
            key = (prediction_city, allocation["wardId"])
            supplies_by_key[key].update({
                key_name: value
                for key_name, value in allocation.items()
                if key_name.endswith("_allocated")
                or key_name.endswith("_deficit")
                or key_name == "criticalSupplyDeficit"
            })

    ward_bed_plan = route_ward_overflow(ward_rows)
    city_bed_utilization = ward_bed_plan["cities"]

    crew_records_query = db.query(CrewAvailability)
    if city:
        crew_records_query = crew_records_query.filter(CrewAvailability.city == city)
    configured_crews = min(500, sum(
        max(0, int(record.available_units or 0))
        for record in crew_records_query.all()
    ))
    crew_count = (
        total_spraying_crews
        if total_spraying_crews is not None
        else configured_crews
    )
    densities_by_city = {
        prediction_city: [
            row["populationDensity"]
            for row in city_rows
            if row["predictedCases"] > 0
        ]
        for prediction_city, city_rows in rows_by_city.items()
    }
    for row in ward_rows:
        city_densities = densities_by_city.get(row["city"], [])
        median_density = float(pd.Series(city_densities).median()) if city_densities else 0.0
        active = row["predictedCases"] > 0
        row["crewNeed"] = (
            int(active)
            + int(active and row["riskCategory"] in {"HIGH", "CRITICAL"})
            + int(active and row["populationDensity"] >= median_density)
        )
    crew_rows = [
        {"crewName": f"Spraying Crew {index + 1}", "availableUnits": 1}
        for index in range(crew_count)
    ]
    crew_plan = plan_crews(ward_rows, crew_rows)
    crew_plan["totalCrews"] = crew_count
    crew_plan["configuredCrews"] = configured_crews
    crew_plan["assignmentPolicy"] = (
        "One crew per active ward, plus one for HIGH/CRITICAL risk and one for "
        "population density at or above the active-ward city median."
    )

    hospital_records_query = db.query(HospitalCapacity)
    if city:
        hospital_records_query = hospital_records_query.filter(HospitalCapacity.city == city)
    hospital_list = []
    for record in hospital_records_query.all():
        available = (
            max(0, record.total_beds - record.occupied_beds)
            if record.total_beds is not None and record.occupied_beds is not None
            else None
        )
        hospital_list.append({
            "city": record.city,
            "hospitalName": record.hospital_name,
            "availableBeds": available,
        })

    overflow = {
        "routes": ward_bed_plan["routes"],
        "recommendations": ward_bed_plan["routes"],
        "unroutedBedDeficit": ward_bed_plan["unroutedBedDeficit"],
    }
    bed_plan = {
        "wards": ward_bed_plan["wards"],
        "cities": city_bed_utilization,
        "status": (
            "unknown"
            if not any(row["availableBeds"] is not None for row in ward_rows)
            else (
                "surge"
                if ward_bed_plan["unroutedBedDeficit"] > 0
                else (
                    "partial"
                    if any(row["capacityCoveragePercent"] < 100 for row in city_bed_utilization)
                    else "covered"
                )
            )
        ),
        "unallocatedDemand": ward_bed_plan["unroutedBedDeficit"],
    }

    supplies = list(supplies_by_key.values())

    return {
        "status": "available" if latest_by_ward else "no-live-predictions",
        "city": city or "all",
        "supplies": supplies,
        "resourceAllocation": {
            "byCity": stock_allocations,
            "availableStockPercent": available_stock_percent,
            "criticalSupplyDeficit": any(
                allocation["criticalSupplyDeficit"]
                for allocation in stock_allocations.values()
            ),
        },
        "bedAllocation": bed_plan,
        "overflow": overflow,
        "crewDeployment": crew_plan,
        "hospitals": hospital_list,
        "cityBedUtilization": city_bed_utilization,
        "scarcityControls": {
            "availableStockPercent": available_stock_percent,
            "totalSprayingCrews": crew_count,
            "stockPercentRange": [30, 100],
        },
        "limitations": [
            "Supply coefficients are configurable planning assumptions, not validated clinical ratios.",
            "The stock percentage is a scenario share of calculated consumable demand, not measured inventory.",
            "Ward bed capacities and occupancy must be configured for accurate local surge routing.",
            "Bed and crew recommendations are planning scenarios, not confirmed dispatch or admission orders.",
        ],
    }


@app.post("/api/admin/hospital-capacity")
async def configure_hospital_capacity(
    payload: HospitalCapacityRequest,
    request: Request,
    x_csrf_token: str = Header(default=""),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if not verify_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    if payload.occupiedBeds is not None and payload.totalBeds is not None and payload.occupiedBeds > payload.totalBeds:
        raise HTTPException(status_code=422, detail="Occupied beds cannot exceed total beds.")
    record = (
        db.query(HospitalCapacity)
        .filter(
            HospitalCapacity.city == payload.city,
            HospitalCapacity.hospital_name == payload.hospitalName,
        )
        .first()
    )
    if record is None:
        record = HospitalCapacity(
            city=payload.city,
            hospital_name=payload.hospitalName,
        )
        db.add(record)
    record.total_beds = payload.totalBeds
    record.occupied_beds = payload.occupiedBeds
    db.commit()
    return {
        "city": record.city,
        "hospitalName": record.hospital_name,
        "totalBeds": record.total_beds,
        "occupiedBeds": record.occupied_beds,
    }


@app.post("/api/admin/ward-bed-capacity")
async def configure_ward_bed_capacity(
    payload: WardBedCapacityRequest,
    request: Request,
    x_csrf_token: str = Header(default=""),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if not verify_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    if payload.occupiedBeds > payload.totalBeds:
        raise HTTPException(status_code=422, detail="Occupied beds cannot exceed total ward beds.")
    record = (
        db.query(WardBedCapacity)
        .filter(
            WardBedCapacity.city == payload.city,
            WardBedCapacity.ward_id == payload.wardId,
        )
        .first()
    )
    if record is None:
        record = WardBedCapacity(city=payload.city, ward_id=payload.wardId)
        db.add(record)
    record.total_beds = payload.totalBeds
    record.occupied_beds = payload.occupiedBeds
    db.commit()
    return {
        "city": record.city,
        "wardId": record.ward_id,
        "totalBeds": record.total_beds,
        "occupiedBeds": record.occupied_beds,
        "availableBeds": record.total_beds - record.occupied_beds,
    }


@app.post("/api/admin/crew-availability")
async def configure_crew_availability(
    payload: CrewAvailabilityRequest,
    request: Request,
    x_csrf_token: str = Header(default=""),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if not verify_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    record = (
        db.query(CrewAvailability)
        .filter(
            CrewAvailability.city == payload.city,
            CrewAvailability.crew_name == payload.crewName,
        )
        .first()
    )
    if record is None:
        record = CrewAvailability(
            city=payload.city,
            crew_name=payload.crewName,
        )
        db.add(record)
    record.available_units = payload.availableUnits
    record.max_wards_per_week = payload.maxWardsPerWeek
    db.commit()
    return {
        "city": record.city,
        "crewName": record.crew_name,
        "availableUnits": record.available_units,
        "maxWardsPerWeek": record.max_wards_per_week,
    }


@app.post("/api/operations/counterfactual")
async def operations_counterfactual(
    payload: CounterfactualRequest,
    request: Request,
    x_csrf_token: str = Header(default=""),
    current_user: User = Depends(get_current_hospital_user),
):
    if not verify_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    """Compare scenario assumptions without presenting simulated effects as facts."""
    supply = calculate_supply(payload.predictedCases)
    baseline_unmet_beds = int(round(supply["beds"]))
    alternative_unmet_beds = max(0, baseline_unmet_beds - payload.additionalBeds)
    benefit = payload.assumedBenefitPoints
    roi = benefit / payload.estimatedCost if payload.estimatedCost > 0 else None
    return {
        "baseline": {
            "predictedCases": payload.predictedCases,
            "unmetBeds": baseline_unmet_beds,
            "crewCoverage": 0,
            "diagnosticKits": supply["diagnosticKits"],
        },
        "scenario": {
            "additionalCrews": payload.additionalCrews,
            "additionalBeds": payload.additionalBeds,
            "additionalDiagnosticKits": payload.additionalDiagnosticKits,
            "unmetBeds": alternative_unmet_beds,
            "bedShortageReduction": baseline_unmet_beds - alternative_unmet_beds,
        },
        "roiRanking": {
            "estimatedCost": payload.estimatedCost,
            "assumedOperationalBenefitPoints": benefit,
            "benefitPerCost": roi,
            "rankLabel": "scenario estimate only",
        },
        "assumptions": {
            "supply": supply["assumptions"],
            "benefit": "Operator supplied assumed benefit points; no causal intervention coefficient is inferred.",
            "cost": "Operator supplied estimated cost.",
        },
        "warning": "This is a configurable scenario estimate, not a proven real-world outcome or causal reduction.",
    }


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

Base.metadata.create_all(bind=engine)


def _ensure_live_observation_case_columns() -> None:
    """Add provenance columns to databases created before live cases became optional."""
    columns = {column["name"] for column in inspect(engine).get_columns("live_observations")}
    with engine.begin() as connection:
        if "reported_dengue_cases" not in columns:
            connection.execute(
                text("ALTER TABLE live_observations ADD COLUMN reported_dengue_cases FLOAT")
            )
        if "dengue_case_source" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE live_observations "
                    "ADD COLUMN dengue_case_source VARCHAR(40) NOT NULL DEFAULT 'reported'"
                )
            )
        connection.execute(
            text(
                "UPDATE live_observations SET dengue_case_source = 'reported' "
                "WHERE dengue_case_source IS NULL"
            )
        )


_ensure_live_observation_case_columns()


def _sync_ward_contexts() -> None:
    """Populate static ward characteristics from the processed dataset only."""
    if not DATA_PATH.is_file():
        return
    try:
        frame = pd.read_csv(DATA_PATH)
        required = {
            "city",
            "ward_id",
            "population_density_sqkm",
            "water_logging_risk_index",
            "sanitation_score",
        }
        if not required.issubset(frame.columns):
            return
        session = SessionLocal()
        try:
            for (city, ward_id), group in frame.groupby(["city", "ward_id"], sort=True):
                latest = group.sort_values("date").iloc[-1]
                record = (
                    session.query(WardContext)
                    .filter(
                        WardContext.city == str(city),
                        WardContext.ward_id == str(ward_id),
                    )
                    .first()
                )
                if record is None:
                    record = WardContext(city=str(city), ward_id=str(ward_id))
                    session.add(record)
                record.population_density_sqkm = float(latest["population_density_sqkm"])
                record.water_logging_risk_index = float(latest["water_logging_risk_index"])
                record.sanitation_score = float(latest["sanitation_score"])
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("Could not synchronize database-backed ward context.")


_sync_ward_contexts()
