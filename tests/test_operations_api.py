"""API tests for regional scarcity controls and ward-level surge routing."""

from __future__ import annotations

import os
import re
from datetime import date
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SESSION_SECRET"] = "operations-api-tests-secret-at-least-32-characters"
os.environ["COOKIE_SECURE"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from src.backend.database import Base, get_db
from src.backend.models import (
    LiveObservation,
    LivePrediction,
    WardBedCapacity,
    WardContext,
)


@pytest.fixture
def operations_client():
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

    main.app.dependency_overrides[get_db] = override_db
    main.app.dependency_overrides[main.get_current_hospital_user] = lambda: SimpleNamespace(id=1)
    main.app.dependency_overrides[main.get_current_admin_user] = lambda: SimpleNamespace(id=2)
    with test_sessions() as db:
        for ward_id, cases, risk, density in (
            ("Surge", 40, "HIGH", 5000),
            ("Receiver", 5, "LOW", 500),
        ):
            observation = LiveObservation(
                city="Dhaka North",
                ward_id=ward_id,
                observation_date=date(2026, 11, 16),
                avg_temp_c=27,
                max_temp_c=32,
                min_temp_c=23,
                rainfall_mm=10,
                humidity_pct=70,
                dengue_cases=1,
            )
            db.add(observation)
            db.flush()
            db.add(LivePrediction(
                observation_id=observation.id,
                city="Dhaka North",
                ward_id=ward_id,
                observation_date=observation.observation_date,
                target_date=date(2026, 11, 30),
                horizon_weeks=2,
                predicted_cases=cases,
                raw_predicted_cases=float(cases),
                risk_category=risk,
                model_name="CatBoost",
                experiment_id="test-run",
                model_artifact="models/test.cbm",
            ))
            db.add(WardContext(
                city="Dhaka North",
                ward_id=ward_id,
                population_density_sqkm=density,
                water_logging_risk_index=0.5,
                sanitation_score=0.5,
            ))
        db.add_all([
            WardBedCapacity(
                city="Dhaka North", ward_id="Surge", total_beds=10, occupied_beds=8
            ),
            WardBedCapacity(
                city="Dhaka North", ward_id="Receiver", total_beds=10, occupied_beds=0
            ),
        ])
        db.commit()
    try:
        with TestClient(main.app) as client:
            yield client, test_sessions
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(main.get_current_hospital_user, None)
        main.app.dependency_overrides.pop(main.get_current_admin_user, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_scarcity_controls_allocate_stock_and_route_bed_overflow(operations_client):
    client, _ = operations_client
    response = client.get(
        "/api/operations/status",
        params={
            "city": "Dhaka North",
            "availableStockPercent": 30,
            "totalSprayingCrews": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    city_stock = data["resourceAllocation"]["byCity"]["Dhaka North"]
    assert city_stock["resources"]["saline_bags"]["needed"] == 90
    assert city_stock["resources"]["saline_bags"]["availableStock"] == 27
    assert city_stock["criticalSupplyDeficit"] is True
    assert data["scarcityControls"]["totalSprayingCrews"] == 1
    assert data["overflow"]["routes"][0]["sourceWardId"] == "Surge"
    assert data["overflow"]["routes"][0]["destinationWardId"] == "Receiver"
    assert data["cityBedUtilization"][0]["bedUtilizationPercent"] == 65.0
    assignments = data["crewDeployment"]["assignments"]
    assert assignments[0]["wardId"] == "Surge"
    assert len(assignments[0]["assignedCrews"]) == 1

    invalid = client.get(
        "/api/operations/status",
        params={"city": "Dhaka North", "availableStockPercent": 29},
    )
    assert invalid.status_code == 422


def test_admin_can_save_local_ward_bed_capacity(operations_client):
    client, sessions = operations_client
    login_page = client.get("/login")
    token = re.search(r'name="csrf_token"\s+value="([^"]+)', login_page.text).group(1)
    response = client.post(
        "/api/admin/ward-bed-capacity",
        headers={"x-csrf-token": token},
        json={
            "city": "Chattogram",
            "wardId": "WARD_02",
            "totalBeds": 40,
            "occupiedBeds": 32,
        },
    )
    assert response.status_code == 200
    assert response.json()["availableBeds"] == 8
    with sessions() as db:
        record = db.query(WardBedCapacity).filter_by(
            city="Chattogram", ward_id="WARD_02"
        ).one()
        assert record.total_beds == 40
        assert record.occupied_beds == 32
