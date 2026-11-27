"""Transparent scenario-based emergency resource calculations."""

from __future__ import annotations

import os
from math import ceil, floor
from typing import Iterable, Mapping


def _number(value: str | None, default: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def supply_assumptions() -> dict:
    """Return labelled planning assumptions from environment configuration."""
    return {
        "salineBagsPerCase": _number(os.getenv("SALINE_BAGS_PER_CASE"), 2.0),
        "diagnosticKitsPerCase": _number(os.getenv("DIAGNOSTIC_KITS_PER_CASE"), 0.25),
        "bedsPerCase": _number(os.getenv("BEDS_PER_CASE"), 0.10),
        "label": "configurable scenario assumptions; not validated causal or clinical coefficients",
    }


def calculate_supply(predicted_cases: float, assumptions: Mapping[str, float] | None = None) -> dict:
    assumptions = assumptions or supply_assumptions()
    cases = max(0.0, float(predicted_cases))
    saline_bags = int(ceil(cases * float(assumptions["salineBagsPerCase"])))
    diagnostic_kits = int(ceil(cases * float(assumptions["diagnosticKitsPerCase"])))
    hospital_beds = int(ceil(cases * float(assumptions["bedsPerCase"])))
    return {
        "predictedCases": cases,
        "salineBags": saline_bags,
        "diagnosticKits": diagnostic_kits,
        "beds": hospital_beds,
        "saline_bags_needed": saline_bags,
        "diagnostic_kits_needed": diagnostic_kits,
        "hospital_beds_required": hospital_beds,
        "assumptions": assumptions,
    }


def priority_key(ward: Mapping) -> tuple:
    """Order by risk, then density, then the transparent numeric priority score."""
    risk_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    risk = risk_rank.get(str(ward.get("riskCategory", "LOW")).upper(), 0)
    density = max(0.0, float(ward.get("populationDensity", 0) or 0))
    score = float(ward.get("priorityScore", 0) or 0)
    return risk, density, score, str(ward.get("wardId", ""))


def allocate_regional_stock(
    ward_demands: Iterable[Mapping],
    available_stock_percent: int,
) -> dict:
    """Allocate integer consumable stock by risk and density without overspending."""
    if not 30 <= int(available_stock_percent) <= 100:
        raise ValueError("Available regional stock must be between 30% and 100%.")

    demand_fields = {
        "saline_bags": "saline_bags_needed",
        "diagnostic_kits": "diagnostic_kits_needed",
    }
    rows = [dict(row) for row in ward_demands]
    totals = {
        resource: sum(max(0, int(row.get(field, 0) or 0)) for row in rows)
        for resource, field in demand_fields.items()
    }
    available = {
        resource: floor(total * int(available_stock_percent) / 100)
        for resource, total in totals.items()
    }
    remaining = dict(available)
    allocated_rows = []
    for row in sorted(rows, key=priority_key, reverse=True):
        allocated = dict(row)
        deficits = []
        for resource, demand_field in demand_fields.items():
            needed = max(0, int(row.get(demand_field, 0) or 0))
            given = min(needed, remaining[resource])
            remaining[resource] -= given
            allocated[f"{resource}_allocated"] = given
            allocated[f"{resource}_deficit"] = needed - given
            if needed > given:
                deficits.append(resource)
        allocated["criticalSupplyDeficit"] = bool(deficits)
        allocated["supplyDeficits"] = deficits
        allocated_rows.append(allocated)

    resource_totals = {
        resource: {
            "needed": totals[resource],
            "availableStock": available[resource],
            "allocated": available[resource] - remaining[resource],
            "deficit": totals[resource] - (available[resource] - remaining[resource]),
        }
        for resource in demand_fields
    }
    return {
        "availableStockPercent": int(available_stock_percent),
        "resources": resource_totals,
        "wardAllocations": allocated_rows,
        "criticalSupplyDeficit": any(row["criticalSupplyDeficit"] for row in allocated_rows),
        "warnings": [
            f"{resource.replace('_', ' ')} demand is not fully covered."
            for resource, totals_for_resource in resource_totals.items()
            if totals_for_resource["deficit"] > 0
        ],
        "assumption": "Available stock is a scenario share of calculated consumable demand, not a measured inventory count.",
    }


def allocate_beds(
    ward_demands: Iterable[Mapping],
    available_beds: int | None,
) -> dict:
    """Allocate integer beds by descending transparent priority, never over capacity."""
    rows = [dict(row) for row in ward_demands]
    if available_beds is None:
        return {
            "availableBeds": None,
            "allocated": [
                {**row, "allocatedBeds": None, "unmetBeds": None, "status": "unknown"}
                for row in rows
            ],
            "unallocatedDemand": None,
            "status": "unknown",
        }

    remaining = max(0, int(available_beds))
    allocated = []
    for row in sorted(rows, key=priority_key, reverse=True):
        demand = max(0, int(round(float(row.get("bedDemand", 0)))))
        given = min(demand, remaining)
        remaining -= given
        allocated.append({
            **row,
            "allocatedBeds": given,
            "unmetBeds": demand - given,
            "status": "met" if demand == given else "shortage",
        })
    return {
        "availableBeds": int(available_beds),
        "remainingBeds": remaining,
        "allocated": allocated,
        "unallocatedDemand": sum(int(row["unmetBeds"]) for row in allocated),
        "status": "sufficient" if all(row["status"] == "met" for row in allocated) else "scarce",
    }


def plan_overflow(
    ward_allocations: Iterable[Mapping],
    hospitals: Iterable[Mapping],
) -> dict:
    """Route unmet bed demand only to hospitals in the same city."""
    hospitals = [dict(row) for row in hospitals]
    remaining = {
        (str(row["city"]), str(row["hospitalName"])): max(
            0,
            int(row["availableBeds"]) if row.get("availableBeds") is not None else 0,
        )
        for row in hospitals
    }
    recommendations = []
    overflow_demand = 0
    for ward in sorted(
        ward_allocations,
        key=lambda item: float(item.get("priorityScore", 0)),
        reverse=True,
    ):
        unmet = max(0, int(ward.get("unmetBeds") or 0))
        overflow_demand += unmet
        city_hospitals = [row for row in hospitals if str(row["city"]) == str(ward["city"])]
        for hospital in sorted(
            city_hospitals,
            key=lambda item: remaining.get(
                (str(item["city"]), str(item["hospitalName"])), 0
            ),
            reverse=True,
        ):
            name = str(hospital["hospitalName"])
            key = (str(hospital["city"]), name)
            route = min(unmet, remaining[key])
            if route:
                recommendations.append({
                    "wardId": ward["wardId"],
                    "hospitalName": name,
                    "city": ward["city"],
                    "recommendedOverflowBeds": route,
                    "type": "same-city-capacity-suggestion",
                })
                unmet -= route
                remaining[key] -= route
    return {
        "recommendations": recommendations,
        "overflowDemand": overflow_demand,
        "unmetOverflowDemand": sum(
            max(0, int(row.get("unmetBeds") or 0))
            for row in ward_allocations
        ) - sum(row["recommendedOverflowBeds"] for row in recommendations),
        "remainingHospitalCapacity": {
            f"{city}::{name}": beds
            for (city, name), beds in remaining.items()
        },
    }


def route_ward_overflow(ward_rows: Iterable[Mapping]) -> dict:
    """Route ward bed deficits to lower-burden wards with known spare capacity."""
    rows = [dict(row) for row in ward_rows]
    by_city: dict[str, list[dict]] = {}
    for row in rows:
        by_city.setdefault(str(row["city"]), []).append(row)

    routes = []
    for city, city_rows in by_city.items():
        remaining_surplus = {
            str(row["wardId"]): max(
                0,
                int(row.get("availableBeds") or 0) - int(row.get("hospital_beds_required", 0) or 0),
            )
            for row in city_rows
            if row.get("availableBeds") is not None
        }
        for source in sorted(city_rows, key=priority_key, reverse=True):
            if source.get("availableBeds") is None:
                continue
            unmet = max(
                0,
                int(source.get("hospital_beds_required", 0) or 0)
                - int(source.get("availableBeds") or 0),
            )
            if not unmet:
                continue
            destinations = sorted(
                (
                    row for row in city_rows
                    if str(row["wardId"]) != str(source["wardId"])
                    and priority_key(row)[:3] < priority_key(source)[:3]
                    and remaining_surplus.get(str(row["wardId"]), 0) > 0
                ),
                key=lambda row: (
                    priority_key(row),
                    -remaining_surplus.get(str(row["wardId"]), 0),
                ),
            )
            for destination in destinations:
                ward_id = str(destination["wardId"])
                transferred = min(unmet, remaining_surplus[ward_id])
                if not transferred:
                    continue
                routes.append({
                    "city": city,
                    "sourceWardId": str(source["wardId"]),
                    "destinationWardId": ward_id,
                    "bedsRouted": transferred,
                    "reason": "lower-burden-ward-with-spare-capacity",
                })
                remaining_surplus[ward_id] -= transferred
                unmet -= transferred
                if not unmet:
                    break

    routed_out: dict[tuple[str, str], int] = {}
    routed_in: dict[tuple[str, str], int] = {}
    for route in routes:
        source_key = (route["city"], route["sourceWardId"])
        destination_key = (route["city"], route["destinationWardId"])
        routed_out[source_key] = routed_out.get(source_key, 0) + route["bedsRouted"]
        routed_in[destination_key] = routed_in.get(destination_key, 0) + route["bedsRouted"]

    ward_results = []
    cities = {}
    for row in rows:
        city = str(row["city"])
        ward_id = str(row["wardId"])
        capacity = row.get("totalBeds")
        occupied = row.get("occupiedBeds")
        demand = int(row.get("hospital_beds_required", 0) or 0)
        incoming = routed_in.get((city, ward_id), 0)
        outgoing = routed_out.get((city, ward_id), 0)
        result = {
            **row,
            "localBedDeficit": (
                max(0, demand - int(row["availableBeds"]))
                if row.get("availableBeds") is not None else None
            ),
            "overflowBedsIn": incoming,
            "overflowBedsOut": outgoing,
            "netBedDeficit": (
                max(0, demand - int(row["availableBeds"]) - outgoing)
                if row.get("availableBeds") is not None else None
            ),
        }
        ward_results.append(result)
        city_total = cities.setdefault(city, {
            "city": city,
            "totalBeds": 0,
            "occupiedBeds": 0,
            "projectedBedDemand": 0,
            "knownWardCount": 0,
            "configuredWardCount": 0,
        })
        if capacity is not None and occupied is not None:
            city_total["totalBeds"] += int(capacity)
            city_total["occupiedBeds"] += int(occupied)
            city_total["projectedBedDemand"] += demand
            city_total["knownWardCount"] += 1
        city_total["configuredWardCount"] += 1

    for summary in cities.values():
        capacity = summary["totalBeds"]
        projected = summary["occupiedBeds"] + summary["projectedBedDemand"]
        summary["projectedOccupiedBeds"] = projected
        summary["bedUtilizationPercent"] = (
            round(projected / capacity * 100, 1) if capacity > 0 else None
        )
        summary["capacityCoveragePercent"] = round(
            summary["knownWardCount"] / summary["configuredWardCount"] * 100, 1
        ) if summary["configuredWardCount"] else 0.0
        summary["netBedDeficit"] = sum(
            int(row["netBedDeficit"] or 0)
            for row in ward_results
            if row["city"] == summary["city"]
        )

    return {
        "routes": routes,
        "wards": ward_results,
        "cities": list(cities.values()),
        "unroutedBedDeficit": sum(
            int(row["netBedDeficit"] or 0)
            for row in ward_results
            if row["netBedDeficit"] is not None
        ),
    }


def plan_crews(
    ward_priorities: Iterable[Mapping],
    crews: Iterable[Mapping],
) -> dict:
    """Assign crews without conflict: at most one active ward assignment per crew."""
    assignments = []
    remaining = {}
    for crew in crews:
        remaining[str(crew["crewName"])] = max(0, int(crew.get("availableUnits") or 0))

    remaining_requirements = []
    for ward in sorted(
        ward_priorities,
        key=priority_key,
        reverse=True,
    ):
        need = max(0, int(round(float(ward.get("crewNeed", 1)))))
        assigned = 0
        crew_names = []
        for crew_name in remaining:
            if assigned >= need:
                break
            if remaining[crew_name] > 0:
                remaining[crew_name] -= 1
                assigned += 1
                crew_names.append(crew_name)
        assignments.append({
            "wardId": ward["wardId"],
            "priorityScore": ward["priorityScore"],
            "assignedCrews": crew_names,
            "unmetDeploymentNeed": need - assigned,
        })
        if need > assigned:
            remaining_requirements.append({"wardId": ward["wardId"], "unmet": need - assigned})

    return {
        "assignments": assignments,
        "remainingCrews": remaining,
        "unmetDeploymentRequirements": remaining_requirements,
    }


def rank_priority(ward: Mapping) -> float:
    """Transparent weighted score; weights are configurable planning weights."""
    predicted = max(0.0, float(ward.get("predictedCases", 0)))
    growth = float(ward.get("growth", 0))
    density = max(0.0, float(ward.get("populationDensity", 0)))
    vulnerability = max(0.0, float(ward.get("waterLoggingRisk", 0))) + max(
        0.0, 1.0 - float(ward.get("sanitationScore", 0))
    )
    return round(
        predicted * 0.50
        + growth * 10.0
        + density / 1000.0 * 0.15
        + vulnerability * 0.20,
        4,
    )
