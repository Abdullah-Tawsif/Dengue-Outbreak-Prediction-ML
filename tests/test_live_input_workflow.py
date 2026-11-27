"""Live-input submission and retry behavior."""

from __future__ import annotations

import os
import re
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SESSION_SECRET"] = "live-input-tests-session-secret-at-least-32-characters"
os.environ["COOKIE_SECURE"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from src.backend.database import Base, get_db
from src.backend.models import LiveObservation, LivePrediction, User


@pytest.fixture
def live_client(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    def override_db():
        db = test_sessions()
        try:
            yield db
        finally:
            db.close()

    with test_sessions() as db:
        db.add(User(
            id=1,
            full_name="Test Hospital",
            hospital_name="Test Hospital",
            email="live-test@example.com",
            hashed_password="not-used",
            is_admin=False,
            is_active=True,
        ))
        db.commit()

    main.app.dependency_overrides[get_db] = override_db
    main.app.dependency_overrides[main.get_current_hospital_user] = lambda: SimpleNamespace(id=1)
    monkeypatch.setattr(main, "get_live_defaults", lambda city, ward: {
        "city": city,
        "wardId": ward,
        "latestHistoricalDate": "2025-07-07",
        "nextExpectedWeeklyDate": "2025-07-14",
        "populationDensity": 100.0,
        "waterLoggingRisk": 0.5,
        "sanitationScore": 0.6,
        "previousCases": 5.0,
        "previousRainfall": 10.0,
        "previousTemperature": 25.0,
        "previousHumidity": 70.0,
    })

    def fake_predict_live(**kwargs):
        assert "dengue_cases" not in kwargs
        return {
            "targetDate": (kwargs["observation_date"] + main.pd.Timedelta(
                weeks=kwargs["horizon_weeks"]
            )).isoformat(),
            "predictedCases": 7,
            "rawPredictedCases": 7.25,
            "riskCategory": "MEDIUM",
            "model": "CatBoost",
            "experimentId": "test-run",
            "modelArtifact": "models/test.cbm",
        }

    monkeypatch.setattr(main, "predict_live", fake_predict_live)

    try:
        with TestClient(main.app) as client:
            yield client, test_sessions
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(main.get_current_hospital_user, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_live_retry_updates_observation_and_prediction_without_case_input(live_client):
    client, test_sessions = live_client
    login_page = client.get("/login")
    token = re.search(r'name="csrf_token"\s+value="([^"]+)', login_page.text).group(1)
    payload = {
        "city": "Dhaka",
        "ward": "ward-1",
        "observationDate": "2025-07-14",
        "horizonWeeks": 2,
        "avgTempC": 27.0,
        "maxTempC": 32.0,
        "minTempC": 23.0,
        "rainfallMm": 15.0,
        "humidityPct": 75.0,
    }

    first = client.post("/api/live-predict", json=payload, headers={"x-csrf-token": token})
    assert first.status_code == 200
    assert first.json()["observationUpdated"] is False

    payload["avgTempC"] = 28.0
    second = client.post("/api/live-predict", json=payload, headers={"x-csrf-token": token})
    assert second.status_code == 200
    assert second.json()["observationUpdated"] is True

    with test_sessions() as db:
        observations = db.query(LiveObservation).all()
        predictions = db.query(LivePrediction).all()
        assert len(observations) == 1
        assert len(predictions) == 1
        assert observations[0].avg_temp_c == 28.0
        assert observations[0].reported_dengue_cases is None
        assert observations[0].dengue_case_source == "context_only"
        assert observations[0].dengue_cases == 5.0

    defaults = client.get("/api/live-input/defaults", params={"city": "Dhaka", "ward": "ward-1"})
    assert defaults.status_code == 200
    assert defaults.json()["latestHistoricalDate"] == "2025-07-14"
    assert defaults.json()["nextExpectedWeeklyDate"] == "2025-07-21"
    assert defaults.json()["latestCaseDate"] == "2025-07-07"


def test_live_input_page_uses_v4_assets_and_includes_notification_actions(live_client):
    client, _ = live_client

    response = client.get("/live-input")

    assert response.status_code == 200
    assert "/static/css/user/live_input.css?v=4" in response.text
    assert "/static/js/user/live_input.js?v=4" in response.text
    assert 'id="smsDraftLink"' in response.text
    assert 'id="whatsappDraftLink"' in response.text
    assert 'id="commissionerBriefLink"' in response.text


def test_existing_live_observations_get_reported_case_provenance(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE live_observations (id INTEGER PRIMARY KEY, dengue_cases FLOAT NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO live_observations (id, dengue_cases) VALUES (1, 4)"
        ))

    monkeypatch.setattr(main, "engine", engine)
    main._ensure_live_observation_case_columns()
    columns = {column["name"] for column in inspect(engine).get_columns("live_observations")}
    with engine.connect() as connection:
        source = connection.execute(text(
            "SELECT dengue_case_source FROM live_observations WHERE id = 1"
        )).scalar_one()

    assert {"reported_dengue_cases", "dengue_case_source"} <= columns
    assert source == "reported"
    engine.dispose()
