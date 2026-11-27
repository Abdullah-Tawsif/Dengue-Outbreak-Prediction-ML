"""Operational response engine for the live early-warning system.

Given a live dengue forecast for a ward, this module converts predicted cases
into a concrete operational plan: supply consumption, scarcity controls, bed
allocation with same-city overflow routing, crew deployment, a
counterfactual "no-intervention" estimate, and an ROI ranking that compares
intervention cost against averted-case burden.

All coefficients are transparent prototype parameters, not clinically validated
constants. They are centralised in OPERATIONAL_PARAMETERS so they can be tuned
without touching the logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from src.live_input_predict import load_historical_source


# ---------------------------------------------------------------------------
# Operational parameters (transparent, tunable, not clinically validated)
# ---------------------------------------------------------------------------
OPERATIONAL_PARAMETERS: dict[str, Any] = {
    # Per-case resource consumption factors.
    "saline_bags_per_case": 2.5,
    "diagnostic_kits_per_case": 1.2,
    "bed_days_per_case": 4.0,
    # A severe-case fraction is applied to bed demand to reflect that only a
    # subset of predicted cases require admission.
    "severe_case_fraction": 0.35,
    # Cost model (currency-neutral units).
    "cost_per_saline_bag": 8.0,
    "cost_per_diagnostic_kit": 15.0,
    "cost_per_bed_day": 120.0,
    "cost_per_crew_day": 200.0,
    "cost_per_overflow_transfer": 350.0,
    # Crew deployment model.
    "cases_per_field_crew": 25.0,
    "min_field_crews": 1,
    "max_field_crews_per_ward": 8,
    "crew_deployment_days": 7,
    # Counterfactual: without intervention, cases are expected to grow by this
    # fraction per week (outbreak-phase assumption).
    "counterfactual_weekly_growth": 0.18,
    # ROI: assumed cases averted per deployed crew per week.
    "cases_averted_per_crew_week": 6.0,
    # Scarcity thresholds (fraction of stock remaining after allocation).
    "scarcity_warning_threshold": 0.20,
    "scarcity_critical_threshold": 0.08,
    # Default per-city capacity (used when no database override is configured).
    "default_beds_per_ward": 40,
    "default_saline_stock_per_city": 5000,
    "default_kit_stock_per_city": 3000,
}


RISK_PRIORITY = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass
class WardForecast:
    """A single ward's live forecast used for operational planning."""
    city: str
    ward_id: str
    horizon_weeks: int
    observation_date: str
    target_date: str
    predicted_cases: float
    risk_category: str
    early_warning: bool


@dataclass
class CityCapacity:
    """Per-city hospital capacity and supply inventory."""
    city: str
    beds_available: int
    saline_bags_in_stock: int
    diagnostic_kits_in_stock: int
    ward_bed_capacities: dict[str, int] = field(default_factory=dict)


def default_city_capacities(cities: list[str]) -> dict[str, CityCapacity]:
    """Build default per-city capacity objects from the operational parameters."""
    params = OPERATIONAL_PARAMETERS
    capacities: dict[str, CityCapacity] = {}
    for city in cities:
        wards = _wards_for_city(city)
        ward_beds = {w: params["default_beds_per_ward"] for w in wards}
        total_beds = sum(ward_beds.values()) if ward_beds else params["default_beds_per_ward"]
        capacities[city] = CityCapacity(
            city=city,
            beds_available=total_beds,
            saline_bags_in_stock=params["default_saline_stock_per_city"],
            diagnostic_kits_in_stock=params["default_kit_stock_per_city"],
            ward_bed_capacities=ward_beds,
        )
    return capacities


def _wards_for_city(city: str) -> list[str]:
    """Return the list of ward IDs belonging to a city from the historical data."""
    try:
        data = load_historical_source()
        wards = (
            data.loc[data.city.astype(str).eq(str(city)), "ward_id"]
            .astype(str)
            .unique()
            .tolist()
        )
        return sorted(wards)
    except Exception:
        return []


def _all_cities() -> list[str]:
    try:
        data = load_historical_source()
        return sorted(data.city.astype(str).unique().tolist())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Supply calculations and scarcity controls
# ---------------------------------------------------------------------------

def calculate_supply_needs(predicted_cases: float) -> dict[str, float]:
    """Convert a predicted case count into consumable supply requirements."""
    p = OPERATIONAL_PARAMETERS
    return {
        "salineBags": max(0.0, predicted_cases * p["saline_bags_per_case"]),
        "diagnosticKits": max(0.0, predicted_cases * p["diagnostic_kits_per_case"]),
    }


def assess_scarcity(
    needs: dict[str, float],
    capacity: CityCapacity,
) -> dict[str, Any]:
    """Compare supply needs against stock and return scarcity status + controls."""
    p = OPERATIONAL_PARAMETERS
    saline = needs["salineBags"]
    kits = needs["diagnosticKits"]

    saline_remaining = capacity.saline_bags_in_stock - saline
    kit_remaining = capacity.diagnostic_kits_in_stock - kits

    def _status(remaining: float, stock: int) -> tuple[str, float]:
        if stock <= 0:
            return "CRITICAL", 0.0
        fraction = remaining / stock
        if fraction <= p["scarcity_critical_threshold"]:
            return "CRITICAL", fraction
        if fraction <= p["scarcity_warning_threshold"]:
            return "WARNING", fraction
        return "OK", fraction

    saline_status, saline_frac = _status(saline_remaining, capacity.saline_bags_in_stock)
    kit_status, kit_frac = _status(kit_remaining, capacity.diagnostic_kits_in_stock)

    controls: list[str] = []
    if saline_status != "OK":
        controls.append(
            f"Ration saline bags: allocate priority wards first; "
            f"{max(0, int(saline_remaining))} units remain ({saline_frac:.0%})."
        )
    if kit_status != "OK":
        controls.append(
            f"Ration diagnostic kits: reserve for severe-suspect cases; "
            f"{max(0, int(kit_remaining))} units remain ({kit_frac:.0%})."
        )
    if saline_status == "CRITICAL" or kit_status == "CRITICAL":
        controls.append("Initiate emergency procurement and inter-city supply transfer request.")

    return {
        "salineBags": {
            "needed": round(saline, 1),
            "inStock": capacity.saline_bags_in_stock,
            "remaining": round(max(0.0, saline_remaining), 1),
            "status": saline_status,
            "remainingFraction": round(saline_frac, 3),
        },
        "diagnosticKits": {
            "needed": round(kits, 1),
            "inStock": capacity.diagnostic_kits_in_stock,
            "remaining": round(max(0.0, kit_remaining), 1),
            "status": kit_status,
            "remainingFraction": round(kit_frac, 3),
        },
        "controls": controls,
    }


# ---------------------------------------------------------------------------
# Bed allocation with same-city overflow routing
# ---------------------------------------------------------------------------

def calculate_bed_demand(predicted_cases: float) -> float:
    """Convert predicted cases into bed-days and then peak beds needed."""
    p = OPERATIONAL_PARAMETERS
    bed_days = predicted_cases * p["severe_case_fraction"] * p["bed_days_per_case"]
    # Spread bed-days across the horizon weeks to get peak concurrent beds.
    return bed_days / max(1, p["crew_deployment_days"] / 7 * 0) or bed_days / 7


def allocate_beds(
    forecast: WardForecast,
    capacity: CityCapacity,
    city_capacities: dict[str, CityCapacity],
) -> dict[str, Any]:
    """Allocate beds for a ward, routing overflow to other wards in the same city."""
    p = OPERATIONAL_PARAMETERS
    bed_days = forecast.predicted_cases * p["severe_case_fraction"] * p["bed_days_per_case"]
    peak_beds = max(1, round(bed_days / 7))  # peak concurrent beds in the target week

    ward_beds = capacity.ward_bed_capacities.get(forecast.ward_id, p["default_beds_per_ward"])
    allocated_locally = min(peak_beds, ward_beds)
    overflow = max(0, peak_beds - allocated_locally)

    overflow_routing: list[dict[str, Any]] = []
    remaining_overflow = overflow

    if remaining_overflow > 0:
        # Same-city overflow: route to other wards with spare capacity.
        other_wards = {
            w: b for w, b in capacity.ward_bed_capacities.items()
            if w != forecast.ward_id
        }
        # Sort wards by available capacity descending.
        sorted_wards = sorted(other_wards.items(), key=lambda kv: kv[1], reverse=True)
        for other_ward, other_beds in sorted_wards:
            if remaining_overflow <= 0:
                break
            spare = other_beds  # assume other wards are empty for this planning step
            routed = min(remaining_overflow, spare)
            if routed > 0:
                overflow_routing.append({
                    "toWard": other_ward,
                    "beds": routed,
                })
                remaining_overflow -= routed

    unallocated = remaining_overflow

    # City-wide utilisation after this allocation.
    total_city_beds = sum(capacity.ward_bed_capacities.values()) or 1
    total_allocated = allocated_locally + sum(r["beds"] for r in overflow_routing)
    city_utilisation = total_allocated / total_city_beds

    return {
        "peakBedsNeeded": peak_beds,
        "wardBedCapacity": ward_beds,
        "allocatedLocally": allocated_locally,
        "overflow": overflow,
        "overflowRouting": overflow_routing,
        "unallocated": unallocated,
        "cityWideUtilisation": round(city_utilisation, 3),
        "totalCityBeds": total_city_beds,
        "city": forecast.city,
    }


# ---------------------------------------------------------------------------
# Crew deployment
# ---------------------------------------------------------------------------

def deploy_crews(forecast: WardForecast) -> dict[str, Any]:
    """Determine field crew deployment for the forecast ward."""
    p = OPERATIONAL_PARAMETERS
    raw_crews = forecast.predicted_cases / p["cases_per_field_crew"]
    crews = max(p["min_field_crews"], min(p["max_field_crews_per_ward"], int(round(raw_crews))))
    if forecast.risk_category in {"HIGH", "CRITICAL"}:
        crews = min(p["max_field_crews_per_ward"], crews + 1)
    deployment_days = p["crew_deployment_days"]
    cost = crews * deployment_days * p["cost_per_crew_day"]
    return {
        "fieldCrews": crews,
        "deploymentDays": deployment_days,
        "costEstimate": round(cost, 2),
        "priority": RISK_PRIORITY.get(forecast.risk_category, 1),
    }


# ---------------------------------------------------------------------------
# Counterfactual estimate
# ---------------------------------------------------------------------------

def counterfactual_estimate(forecast: WardForecast) -> dict[str, Any]:
    """Estimate cases if no intervention is deployed over the forecast horizon."""
    p = OPERATIONAL_PARAMETERS
    growth = p["counterfactual_weekly_growth"]
    # No-intervention cases grow weekly from the current predicted level.
    no_intervention_cases = forecast.predicted_cases * ((1 + growth) ** forecast.horizon_weeks)
    averted = max(0.0, no_intervention_cases - forecast.predicted_cases)
    return {
        "noInterventionCases": round(no_intervention_cases, 1),
        "interventionCases": round(forecast.predicted_cases, 1),
        "casesAverted": round(averted, 1),
        "assumption": (
            f"Without intervention, weekly cases grow {growth:.0%} per week "
            f"over the {forecast.horizon_weeks}-week horizon."
        ),
    }


# ---------------------------------------------------------------------------
# ROI ranking
# ---------------------------------------------------------------------------

def roi_ranking(
    forecast: WardForecast,
    supply: dict[str, Any],
    beds: dict[str, Any],
    crews: dict[str, Any],
    counterfactual: dict[str, Any],
) -> dict[str, Any]:
    """Rank the intervention by return on investment (averted burden vs cost)."""
    p = OPERATIONAL_PARAMETERS
    supply_cost = (
        supply["salineBags"]["needed"] * p["cost_per_saline_bag"]
        + supply["diagnosticKits"]["needed"] * p["cost_per_diagnostic_kit"]
    )
    bed_cost = beds["peakBedsNeeded"] * 7 * p["cost_per_bed_day"]
    overflow_cost = len(beds["overflowRouting"]) * p["cost_per_overflow_transfer"]
    crew_cost = crews["costEstimate"]
    total_cost = supply_cost + bed_cost + overflow_cost + crew_cost

    cases_averted = counterfactual["casesAverted"]
    # ROI = (value of averted cases - cost) / cost; value uses crew-based averted estimate.
    crew_averted = crews["fieldCrews"] * p["cases_averted_per_crew_week"]
    effective_averted = min(cases_averted, crew_averted) if cases_averted > 0 else crew_averted
    # Monetised burden per averted case (proxy: bed-day cost * severe fraction).
    burden_per_case = p["cost_per_bed_day"] * p["bed_days_per_case"] * p["severe_case_fraction"]
    value_averted = effective_averted * burden_per_case
    roi = (value_averted - total_cost) / total_cost if total_cost > 0 else 0.0

    return {
        "totalCost": round(total_cost, 2),
        "costBreakdown": {
            "supplies": round(supply_cost, 2),
            "beds": round(bed_cost, 2),
            "overflowTransfers": round(overflow_cost, 2),
            "crews": round(crew_cost, 2),
        },
        "estimatedCasesAverted": round(effective_averted, 1),
        "valueAverted": round(value_averted, 2),
        "roi": round(roi, 3),
        "roiLabel": "positive" if roi > 0 else "neutral" if roi == 0 else "negative",
        "rank": _rank_label(roi),
    }


def _rank_label(roi: float) -> str:
    if roi >= 1.0:
        return "HIGH PRIORITY"
    if roi >= 0.3:
        return "MEDIUM PRIORITY"
    if roi >= 0.0:
        return "LOW PRIORITY"
    return "DEPRIORITISE"


# ---------------------------------------------------------------------------
# Full operational plan
# ---------------------------------------------------------------------------

def build_operational_plan(
    forecast: WardForecast,
    city_capacities: dict[str, CityCapacity] | None = None,
) -> dict[str, Any]:
    """Build the complete operational plan for a single ward forecast."""
    if city_capacities is None:
        city_capacities = default_city_capacities(_all_cities())

    capacity = city_capacities.get(forecast.city)
    if capacity is None:
        capacity = CityCapacity(
            city=forecast.city,
            beds_available=OPERATIONAL_PARAMETERS["default_beds_per_ward"],
            saline_bags_in_stock=OPERATIONAL_PARAMETERS["default_saline_stock_per_city"],
            diagnostic_kits_in_stock=OPERATIONAL_PARAMETERS["default_kit_stock_per_city"],
            ward_bed_capacities={forecast.ward_id: OPERATIONAL_PARAMETERS["default_beds_per_ward"]},
        )
        city_capacities[forecast.city] = capacity

    supply_needs = calculate_supply_needs(forecast.predicted_cases)
    scarcity = assess_scarcity(supply_needs, capacity)
    beds = allocate_beds(forecast, capacity, city_capacities)
    crews = deploy_crews(forecast)
    counterfactual = counterfactual_estimate(forecast)
    roi = roi_ranking(forecast, scarcity, beds, crews, counterfactual)

    return {
        "forecast": {
            "city": forecast.city,
            "wardId": forecast.ward_id,
            "horizonWeeks": forecast.horizon_weeks,
            "observationDate": forecast.observation_date,
            "targetDate": forecast.target_date,
            "predictedCases": forecast.predicted_cases,
            "riskCategory": forecast.risk_category,
            "earlyWarning": forecast.early_warning,
        },
        "supply": scarcity,
        "bedAllocation": beds,
        "crewDeployment": crews,
        "counterfactual": counterfactual,
        "roi": roi,
        "parameters": {
            "salineBagsPerCase": OPERATIONAL_PARAMETERS["saline_bags_per_case"],
            "diagnosticKitsPerCase": OPERATIONAL_PARAMETERS["diagnostic_kits_per_case"],
            "severeCaseFraction": OPERATIONAL_PARAMETERS["severe_case_fraction"],
            "bedDaysPerCase": OPERATIONAL_PARAMETERS["bed_days_per_case"],
            "counterfactualWeeklyGrowth": OPERATIONAL_PARAMETERS["counterfactual_weekly_growth"],
        },
        "disclaimer": (
            "Operational figures are transparent prototype estimates from "
            "configurable parameters, not clinically validated thresholds."
        ),
    }


def build_multi_ward_plan(
    forecasts: list[WardForecast],
    city_capacities: dict[str, CityCapacity] | None = None,
) -> dict[str, Any]:
    """Build operational plans for multiple wards and rank them by ROI."""
    if city_capacities is None:
        city_capacities = default_city_capacities(_all_cities())

    plans = []
    for fc in forecasts:
        plan = build_operational_plan(fc, city_capacities)
        plans.append(plan)

    # Rank by ROI descending, then by risk priority.
    plans.sort(
        key=lambda p: (
            -p["roi"]["roi"],
            -RISK_PRIORITY.get(p["forecast"]["riskCategory"], 1),
        ),
    )
    return {
        "rankedPlans": plans,
        "cityUtilisation": _city_utilisation_summary(plans, city_capacities),
        "disclaimer": plans[0]["disclaimer"] if plans else "",
    }


def _city_utilisation_summary(
    plans: list[dict[str, Any]],
    city_capacities: dict[str, CityCapacity],
) -> list[dict[str, Any]]:
    """Summarise bed utilisation across all cities in the plan set."""
    summary: dict[str, dict[str, Any]] = {}
    for plan in plans:
        beds = plan["bedAllocation"]
        city = beds["city"]
        if city not in summary:
            summary[city] = {
                "city": city,
                "totalBeds": beds["totalCityBeds"],
                "allocatedBeds": 0,
                "overflowBeds": 0,
                "wardsPlanned": 0,
            }
        summary[city]["allocatedBeds"] += beds["allocatedLocally"]
        summary[city]["overflowBeds"] += beds["overflow"]
        summary[city]["wardsPlanned"] += 1

    result = []
    for entry in summary.values():
        total = entry["totalBeds"] or 1
        result.append({
            "city": entry["city"],
            "totalBeds": entry["totalBeds"],
            "allocatedBeds": entry["allocatedBeds"],
            "overflowBeds": entry["overflowBeds"],
            "utilisation": round((entry["allocatedBeds"] + entry["overflowBeds"]) / total, 3),
            "wardsPlanned": entry["wardsPlanned"],
        })
    return sorted(result, key=lambda c: c["utilisation"], reverse=True)
