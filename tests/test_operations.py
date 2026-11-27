"""Tests for the operational response engine and live-input API wiring."""

from __future__ import annotations

import os
import re
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SESSION_SECRET"] = "ops-tests-only-session-secret-at-least-32-characters"
os.environ["COOKIE_SECURE"] = "false"

import pytest
from fastapi.testclient import TestClient

import main
from src.operations import (
    OPERATIONAL_PARAMETERS,
    CityCapacity,
    WardForecast,
    allocate_beds,
    assess_scarcity,
    build_multi_ward_plan,
    build_operational_plan,
    calculate_supply_needs,
    counterfactual_estimate,
    deploy_crews,
    roi_ranking,
)


def _forecast(cases=50, risk="HIGH", horizon=2):
    return WardForecast(
        city="Chattogram",
        ward_id="CCC_Ward_01",
        horizon_weeks=horizon,
        observation_date="2025-12-01",
        target_date="2025-12-15",
        predicted_cases=cases,
        risk_category=risk,
        early_warning=risk in {"HIGH", "CRITICAL"},
    )


def _capacity():
    return CityCapacity(
        city="Chattogram",
        beds_available=120,
        saline_bags_in_stock=5000,
        diagnostic_kits_in_stock=3000,
        ward_bed_capacities={"CCC_Ward_01": 40, "CCC_Ward_02": 40, "CCC_Ward_03": 40},
    )


# ---------------------------------------------------------------------------
# Supply and scarcity
# ---------------------------------------------------------------------------

def test_supply_needs_scale_with_cases():
    needs = calculate_supply_needs(100)
    p = OPERATIONAL_PARAMETERS
    assert needs["salineBags"] == pytest.approx(100 * p["saline_bags_per_case"])
    assert needs["diagnosticKits"] == pytest.approx(100 * p["diagnostic_kits_per_case"])


def test_scarcity_ok_when_stock_exceeds_need():
    needs = calculate_supply_needs(10)
    scarcity = assess_scarcity(needs, _capacity())
    assert scarcity["salineBags"]["status"] == "OK"
    assert scarcity["diagnosticKits"]["status"] == "OK"
    assert scarcity["controls"] == []


def test_scarcity_warning_when_stock_low():
    cap = _capacity()
    cap.saline_bags_in_stock = 30  # 50 needed → remaining fraction < 0.20
    needs = calculate_supply_needs(50)
    scarcity = assess_scarcity(needs, cap)
    assert scarcity["salineBags"]["status"] in {"WARNING", "CRITICAL"}
    assert len(scarcity["controls"]) >= 1


def test_scarcity_critical_triggers_emergency_procurement():
    cap = _capacity()
    cap.saline_bags_in_stock = 10
    cap.diagnostic_kits_in_stock = 5
    needs = calculate_supply_needs(50)
    scarcity = assess_scarcity(needs, cap)
    assert scarcity["salineBags"]["status"] == "CRITICAL"
    assert any("emergency procurement" in c for c in scarcity["controls"])


# ---------------------------------------------------------------------------
# Bed allocation and overflow routing
# ---------------------------------------------------------------------------

def test_bed_allocation_fits_locally():
    fc = _forecast(cases=20, risk="MEDIUM")
    beds = allocate_beds(fc, _capacity(), {"Chattogram": _capacity()})
    assert beds["allocatedLocally"] <= beds["wardBedCapacity"]
    assert beds["overflow"] >= 0
    assert beds["unallocated"] >= 0


def test_bed_overflow_routes_to_same_city_wards():
    fc = _forecast(cases=500, risk="CRITICAL")
    cap = _capacity()
    cap.ward_bed_capacities = {"CCC_Ward_01": 10, "CCC_Ward_02": 30, "CCC_Ward_03": 20}
    beds = allocate_beds(fc, cap, {"Chattogram": cap})
    assert beds["overflow"] > 0
    assert len(beds["overflowRouting"]) > 0
    for route in beds["overflowRouting"]:
        assert route["toWard"] != "CCC_Ward_01"
        assert route["beds"] > 0
    assert beds["cityWideUtilisation"] > 0


def test_city_utilisation_capped_at_total_beds():
    fc = _forecast(cases=1000, risk="CRITICAL")
    cap = _capacity()
    beds = allocate_beds(fc, cap, {"Chattogram": cap})
    assert beds["cityWideUtilisation"] <= 1.0


# ---------------------------------------------------------------------------
# Crew deployment
# ---------------------------------------------------------------------------

def test_crew_deployment_scales_with_cases():
    fc = _forecast(cases=100, risk="HIGH")
    crews = deploy_crews(fc)
    p = OPERATIONAL_PARAMETERS
    assert crews["fieldCrews"] >= p["min_field_crews"]
    assert crews["fieldCrews"] <= p["max_field_crews_per_ward"]
    assert crews["costEstimate"] > 0


def test_crew_deployment_boosted_for_high_risk():
    low = deploy_crews(_forecast(cases=25, risk="LOW"))
    high = deploy_crews(_forecast(cases=25, risk="HIGH"))
    assert high["fieldCrews"] >= low["fieldCrews"]


# ---------------------------------------------------------------------------
# Counterfactual and ROI
# ---------------------------------------------------------------------------

def test_counterfactual_grows_without_intervention():
    cf = counterfactual_estimate(_forecast(cases=50, horizon=2))
    assert cf["noInterventionCases"] > cf["interventionCases"]
    assert cf["casesAverted"] > 0


def test_roi_ranking_returns_valid_labels():
    fc = _forecast(cases=80, risk="HIGH")
    cap = _capacity()
    needs = calculate_supply_needs(fc.predicted_cases)
    scarcity = assess_scarcity(needs, cap)
    beds = allocate_beds(fc, cap, {"Chattogram": cap})
    crews = deploy_crews(fc)
    cf = counterfactual_estimate(fc)
    roi = roi_ranking(fc, scarcity, beds, crews, cf)
    assert roi["totalCost"] > 0
    assert roi["rank"] in {"HIGH PRIORITY", "MEDIUM PRIORITY", "LOW PRIORITY", "DEPRIORITISE"}
    assert roi["roiLabel"] in {"positive", "neutral", "negative"}


# ---------------------------------------------------------------------------
# Full operational plan
# ---------------------------------------------------------------------------

def test_build_operational_plan_has_all_sections():
    plan = build_operational_plan(_forecast(cases=60, risk="HIGH"))
    assert "forecast" in plan
    assert "supply" in plan
    assert "bedAllocation" in plan
    assert "crewDeployment" in plan
    assert "counterfactual" in plan
    assert "roi" in plan
    assert "disclaimer" in plan
    assert isinstance(plan["supply"]["controls"], list)


def test_multi_ward_plan_ranks_by_roi():
    forecasts = [
        _forecast(cases=10, risk="LOW", horizon=2),
        _forecast(cases=80, risk="HIGH", horizon=3),
        _forecast(cases=200, risk="CRITICAL", horizon=4),
    ]
    forecasts[0].ward_id = "CCC_Ward_02"
    forecasts[1].ward_id = "CCC_Ward_03"
    multi = build_multi_ward_plan(forecasts)
    assert len(multi["rankedPlans"]) == 3
    assert "cityUtilisation" in multi
    # Plans should be sorted by ROI descending
    rois = [p["roi"]["roi"] for p in multi["rankedPlans"]]
    assert rois == sorted(rois, reverse=True)


# ---------------------------------------------------------------------------
# Live-input API integration (offline, no internet needed)
# ---------------------------------------------------------------------------

def test_live_input_page_requires_auth():
    with TestClient(main.app) as client:
        resp = client.get("/live-input", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


def test_live_input_page_renders_for_hospital_user():
    user = SimpleNamespace(
        full_name="Test User", hospital_name="Test Hospital",
        email="test@example.invalid", is_admin=False, id=1,
    )
    main.app.dependency_overrides[main.get_current_hospital_user] = lambda: user
    try:
        with TestClient(main.app) as client:
            resp = client.get("/live-input")
            assert resp.status_code == 200
            assert "Live Early Warning" in resp.text or "LIVE EARLY WARNING" in resp.text
            assert 'id="liveForm"' in resp.text
            assert 'id="operations"' in resp.text
    finally:
        main.app.dependency_overrides.pop(main.get_current_hospital_user, None)


def test_live_input_defaults_endpoint_requires_auth():
    with TestClient(main.app) as client:
        resp = client.get("/api/live-input/defaults", params={"city": "Chattogram", "ward": "CCC_Ward_01"})
        assert resp.status_code == 303


def test_live_predict_endpoint_enforces_csrf():
    user = SimpleNamespace(
        full_name="Test User", hospital_name="Test Hospital",
        email="test@example.invalid", is_admin=False, id=1,
    )
    main.app.dependency_overrides[main.get_current_hospital_user] = lambda: user
    try:
        with TestClient(main.app) as client:
            resp = client.post("/api/live-predict", json={
                "city": "Chattogram", "ward": "CCC_Ward_01",
                "observationDate": "2025-12-29", "horizonWeeks": 2,
                "avgTempC": 25, "maxTempC": 30, "minTempC": 20,
                "rainfallMm": 50, "humidityPct": 80, "dengueCases": 5,
            }, headers={"x-csrf-token": "invalid"})
            assert resp.status_code == 403
    finally:
        main.app.dependency_overrides.pop(main.get_current_hospital_user, None)


def test_live_predict_returns_forecast_and_operations():
    user = SimpleNamespace(
        full_name="Test User", hospital_name="Test Hospital",
        email="test@example.invalid", is_admin=False, id=1,
    )
    main.app.dependency_overrides[main.get_current_hospital_user] = lambda: user
    try:
        with TestClient(main.app) as client:
            login = client.get("/login")
            token = re.search(r'name="csrf_token"\s+value="([^"]+)', login.text).group(1)

            # First load defaults to get the expected observation date.
            defaults = client.get("/api/live-input/defaults", params={
                "city": "Chattogram", "ward": "CCC_Ward_01",
            })
            assert defaults.status_code == 200
            obs_date = defaults.json()["nextExpectedWeeklyDate"]

            resp = client.post("/api/live-predict", json={
                "city": "Chattogram", "ward": "CCC_Ward_01",
                "observationDate": obs_date, "horizonWeeks": 2,
                "avgTempC": 25.0, "maxTempC": 30.0, "minTempC": 20.0,
                "rainfallMm": 50.0, "humidityPct": 80.0, "dengueCases": 5.0,
            }, headers={"x-csrf-token": token})

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["city"] == "Chattogram"
            assert body["wardId"] == "CCC_Ward_01"
            assert "predictedCases" in body
            assert "riskCategory" in body
            assert "operations" in body
            assert body["operations"] is not None
            assert "supply" in body["operations"]
            assert "bedAllocation" in body["operations"]
            assert "crewDeployment" in body["operations"]
            assert "counterfactual" in body["operations"]
            assert "roi" in body["operations"]
            assert body["operations"]["roi"]["rank"] in {
                "HIGH PRIORITY", "MEDIUM PRIORITY", "LOW PRIORITY", "DEPRIORITISE"
            }
    finally:
        main.app.dependency_overrides.pop(main.get_current_hospital_user, None)
