"""Tests for transparent operational planning calculations."""

from src.operations import (
    allocate_beds,
    allocate_regional_stock,
    calculate_supply,
    plan_overflow,
    plan_crews,
    rank_priority,
    route_ward_overflow,
)


def test_supply_needs_are_whole_units_rounded_up():
    supply = calculate_supply(5)
    assert supply["saline_bags_needed"] == 10
    assert supply["diagnostic_kits_needed"] == 2
    assert supply["hospital_beds_required"] == 1


def test_regional_stock_caps_allocations_and_warns_about_deficits():
    allocation = allocate_regional_stock(
        [
            {
                "city": "Dhaka North",
                "wardId": "HIGH",
                "riskCategory": "HIGH",
                "populationDensity": 5000,
                "priorityScore": 10,
                "saline_bags_needed": 5,
                "diagnostic_kits_needed": 2,
            },
            {
                "city": "Dhaka North",
                "wardId": "LOW",
                "riskCategory": "LOW",
                "populationDensity": 500,
                "priorityScore": 2,
                "saline_bags_needed": 5,
                "diagnostic_kits_needed": 2,
            },
        ],
        30,
    )
    assert allocation["resources"]["saline_bags"]["availableStock"] == 3
    assert allocation["wardAllocations"][0]["wardId"] == "HIGH"
    assert allocation["wardAllocations"][0]["saline_bags_allocated"] == 3
    assert allocation["criticalSupplyDeficit"] is True
    assert allocation["warnings"]


def test_overflow_routes_only_to_lower_burden_wards_in_the_same_city():
    plan = route_ward_overflow(
        [
            {
                "city": "Dhaka North",
                "wardId": "Surge",
                "riskCategory": "CRITICAL",
                "populationDensity": 5000,
                "priorityScore": 20,
                "hospital_beds_required": 5,
                "totalBeds": 10,
                "occupiedBeds": 8,
                "availableBeds": 2,
            },
            {
                "city": "Dhaka North",
                "wardId": "Receiver",
                "riskCategory": "LOW",
                "populationDensity": 500,
                "priorityScore": 1,
                "hospital_beds_required": 1,
                "totalBeds": 10,
                "occupiedBeds": 0,
                "availableBeds": 10,
            },
            {
                "city": "Dhaka South",
                "wardId": "Other City",
                "riskCategory": "LOW",
                "populationDensity": 100,
                "priorityScore": 0,
                "hospital_beds_required": 1,
                "totalBeds": 50,
                "occupiedBeds": 0,
                "availableBeds": 50,
            },
        ]
    )
    assert plan["routes"] == [{
        "city": "Dhaka North",
        "sourceWardId": "Surge",
        "destinationWardId": "Receiver",
        "bedsRouted": 3,
        "reason": "lower-burden-ward-with-spare-capacity",
    }]
    surge = next(row for row in plan["wards"] if row["wardId"] == "Surge")
    assert surge["localBedDeficit"] == 3
    assert surge["netBedDeficit"] == 0
    north = next(row for row in plan["cities"] if row["city"] == "Dhaka North")
    assert north["bedUtilizationPercent"] == 70.0


def test_bed_allocation_never_exceeds_capacity() -> None:
    plan = allocate_beds(
        [
            {"city": "Dhaka", "wardId": "A", "bedDemand": 6, "priorityScore": 8},
            {"city": "Dhaka", "wardId": "B", "bedDemand": 3, "priorityScore": 5},
        ],
        5,
    )
    assert plan["allocated"][0]["wardId"] == "A"
    assert plan["allocated"][0]["allocatedBeds"] == 5
    assert plan["allocated"][1]["allocatedBeds"] == 0
    assert plan["remainingBeds"] == 0
    assert plan["unallocatedDemand"] == 4


def test_overflow_stays_in_same_city_and_respects_hospital_capacity() -> None:
    plan = plan_overflow(
        [
            {
                "city": "Dhaka",
                "wardId": "A",
                "unmetBeds": 4,
                "priorityScore": 8,
            }
        ],
        [
            {"city": "Dhaka", "hospitalName": "Hospital 1", "availableBeds": 3},
            {"city": "Chattogram", "hospitalName": "Hospital 2", "availableBeds": 30},
        ],
    )
    assert plan["recommendations"][0]["recommendedOverflowBeds"] == 3
    assert plan["unmetOverflowDemand"] == 1


def test_crew_assignment_does_not_conflict() -> None:
    plan = plan_crews(
        [
            {"wardId": "A", "priorityScore": 8, "crewNeed": 2},
            {"wardId": "B", "priorityScore": 6, "crewNeed": 1},
        ],
        [{"crewName": "Team 1", "availableUnits": 1}],
    )
    assigned_crews = [crew for row in plan["assignments"] for crew in row["assignedCrews"]]
    assert len(assigned_crews) == len(set(assigned_crews))
    assert plan["remainingCrews"]["Team 1"] == 0


def test_priority_score_is_transparent_and_deterministic() -> None:
    ward = {
        "predictedCases": 20,
        "growth": 0.5,
        "populationDensity": 3000,
        "waterLoggingRisk": 0.8,
        "sanitationScore": 0.4,
    }
    assert rank_priority(ward) == rank_priority(dict(ward))
