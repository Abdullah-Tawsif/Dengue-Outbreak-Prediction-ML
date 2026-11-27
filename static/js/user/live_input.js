"use strict";

const form = document.getElementById("liveForm");
const city = document.getElementById("city");
const ward = document.getElementById("ward");
const observationDate = document.getElementById("observationDate");
const dateHelp = document.getElementById("dateHelp");
const predictButton = document.getElementById("predictButton");
const formMessage = document.getElementById("formMessage");
const result = document.getElementById("result");
const riskBadge = document.getElementById("riskBadge");
const alertBox = document.getElementById("alertBox");
const previousContext = document.getElementById("previousContext");
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const stockPercent = document.getElementById("availableStockPercent");
const stockPercentLabel = document.getElementById("stockPercentLabel");
const totalSprayingCrews = document.getElementById("totalSprayingCrews");

const areas = JSON.parse(document.body.dataset.areas || "{}");
let scenarioControlsInitialized = false;
let operationsRequestTimer;

function moneylessNumber(value, digits = 2) {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
}

function formatDate(value) {
    return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }).format(new Date(`${value}T00:00:00`));
}

function setMessage(text, error = false) {
    formMessage.textContent = text;
    formMessage.className = `form-message${error ? " error" : ""}`;
}

async function loadOperations(selectedCity, useScenarioControls = false) {
    const summary = document.getElementById("operationsSummary");
    try {
        const params = new URLSearchParams({ city: selectedCity });
        if (useScenarioControls) {
            params.set("availableStockPercent", stockPercent.value);
            params.set("totalSprayingCrews", totalSprayingCrews.value || "0");
        }
        const response = await fetch(`/api/operations/status?${params}`, {
            headers: { "Accept": "application/json" },
            credentials: "same-origin",
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Operational status unavailable.");
        if (!scenarioControlsInitialized) {
            stockPercent.value = String(data.scarcityControls.availableStockPercent);
            totalSprayingCrews.value = String(data.scarcityControls.totalSprayingCrews);
            scenarioControlsInitialized = true;
        }
        stockPercentLabel.textContent = `${stockPercent.value}%`;

        const addLine = (text, className = "") => {
            const line = document.createElement("p");
            line.textContent = text;
            if (className) line.className = className;
            summary.appendChild(line);
        };
        const addTable = (headers, rows) => {
            const table = document.createElement("table");
            const head = document.createElement("thead");
            const headingRow = document.createElement("tr");
            headers.forEach((text) => {
                const cell = document.createElement("th");
                cell.textContent = text;
                headingRow.appendChild(cell);
            });
            head.appendChild(headingRow);
            table.appendChild(head);
            const body = document.createElement("tbody");
            rows.forEach((values) => {
                const row = document.createElement("tr");
                values.forEach((text) => {
                    const cell = document.createElement("td");
                    cell.textContent = String(text);
                    row.appendChild(cell);
                });
                body.appendChild(row);
            });
            table.appendChild(body);
            summary.appendChild(table);
        };

        summary.replaceChildren();
        const cityStock = data.resourceAllocation.byCity[selectedCity];
        addLine(`Resource allocations · ${data.scarcityControls.availableStockPercent}% scenario stock`);
        if (cityStock) {
            addTable(
                ["Resource", "Needed", "Allocated", "Deficit"],
                Object.entries(cityStock.resources).map(([name, resource]) => [
                    name.replaceAll("_", " "),
                    resource.needed,
                    resource.allocated,
                    resource.deficit,
                ])
            );
            cityStock.warnings.forEach((warning) => addLine(`Critical supply deficit: ${warning}`, "operations-warning"));
            addTable(
                ["Ward", "Saline needed / allocated", "Kits needed / allocated", "Critical deficit"],
                cityStock.wardAllocations.map((entry) => [
                    entry.wardId,
                    `${entry.saline_bags_needed} / ${entry.saline_bags_allocated}`,
                    `${entry.diagnostic_kits_needed} / ${entry.diagnostic_kits_allocated}`,
                    entry.criticalSupplyDeficit ? "YES" : "No",
                ])
            );
            cityStock.wardAllocations
                .filter((entry) => entry.criticalSupplyDeficit)
                .forEach((entry) => addLine(`Critical supply deficit: ${entry.wardId} is not fully covered.`, "operations-warning"));
        } else {
            addLine("No persisted live prediction is available for this city.");
        }

        const utilization = data.cityBedUtilization.find((entry) => entry.city === selectedCity);
        if (utilization) {
            const rate = utilization.bedUtilizationPercent === null
                ? "unknown"
                : `${moneylessNumber(utilization.bedUtilizationPercent, 1)}%`;
            addLine(
                `City-wide projected bed utilization: ${rate} · ${utilization.projectedOccupiedBeds}/${utilization.totalBeds} beds · ward-capacity coverage ${utilization.capacityCoveragePercent}%`
            );
        } else {
            addLine("City-wide bed utilization unavailable until ward bed capacities are configured.");
        }
        const cityWards = data.bedAllocation.wards.filter((entry) => entry.city === selectedCity);
        if (cityWards.length) {
            addTable(
                ["Ward", "Bed demand", "Available", "Local deficit", "Net deficit"],
                cityWards.map((entry) => [
                    entry.wardId,
                    entry.hospital_beds_required,
                    entry.availableBeds ?? "unknown",
                    entry.localBedDeficit ?? "unknown",
                    entry.netBedDeficit ?? "unknown",
                ])
            );
        }
        const routes = data.overflow.routes.filter((entry) => entry.city === selectedCity);
        if (data.bedAllocation.unallocatedDemand > 0) {
            addLine(
                `Critical bed deficit: ${data.bedAllocation.unallocatedDemand} projected bed(s) remain unrouted.`,
                "operations-warning"
            );
        }
        addLine(
            routes.length
                ? `Same-city overflow: ${routes.map((route) => `${route.sourceWardId} → ${route.destinationWardId} (${route.bedsRouted} beds)`).join("; ")}`
                : "Same-city overflow: no ward-to-ward routes recommended."
        );
        const crewRows = data.crewDeployment.assignments;
        addLine(
            `Spraying crews: ${data.crewDeployment.totalCrews} available · ${crewRows.reduce((sum, row) => sum + row.assignedCrews.length, 0)} assigned`
        );
        if (crewRows.length) {
            addTable(
                ["Ward", "Risk", "Crew need", "Assigned", "Unmet"],
                crewRows.map((entry) => {
                    const ward = data.bedAllocation.wards.find((row) => row.wardId === entry.wardId && row.city === selectedCity);
                    return [
                        entry.wardId,
                        ward?.riskCategory || "—",
                        ward?.crewNeed ?? "—",
                        entry.assignedCrews.length,
                        entry.unmetDeploymentNeed,
                    ];
                })
            );
        }
        addLine("Demand ratios are planning assumptions, not validated clinical coefficients. Routing and crew assignments are recommendations, not dispatch orders.");
    } catch (error) {
        summary.textContent = error.message;
    }
}

function scheduleOperationsRefresh() {
    scenarioControlsInitialized = true;
    stockPercentLabel.textContent = `${stockPercent.value}%`;
    window.clearTimeout(operationsRequestTimer);
    operationsRequestTimer = window.setTimeout(
        () => city.value && loadOperations(city.value, true),
        180
    );
}

stockPercent.addEventListener("input", scheduleOperationsRefresh);
totalSprayingCrews.addEventListener("input", scheduleOperationsRefresh);

function populateWards() {
    ward.innerHTML = '<option value="">Select ward</option>';
    (areas[city.value] || []).forEach((wardId) => {
        const option = document.createElement("option");
        option.value = wardId;
        option.textContent = wardId;
        ward.appendChild(option);
    });
    ward.disabled = !city.value;
    observationDate.value = "";
    previousContext.hidden = true;
}

async function loadDefaults() {
    if (!city.value || !ward.value) return;
    setMessage("Loading ward context…");
    try {
        const response = await fetch(`/api/live-input/defaults?city=${encodeURIComponent(city.value)}&ward=${encodeURIComponent(ward.value)}`, {
            headers: { "Accept": "application/json" },
            credentials: "same-origin",
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not load ward defaults.");

        observationDate.value = data.nextExpectedWeeklyDate;
        observationDate.min = data.nextExpectedWeeklyDate;
        dateHelp.textContent = `Latest saved observation: ${formatDate(data.latestHistoricalDate)}. The live row must use ${formatDate(data.nextExpectedWeeklyDate)}.`;
        previousContext.hidden = false;
        previousContext.innerHTML = `Latest confirmed dengue history (${formatDate(data.latestCaseDate)}): <strong>${moneylessNumber(data.previousCases, 0)} cases</strong> · ${moneylessNumber(data.previousRainfall)} mm rain · ${moneylessNumber(data.previousTemperature)} °C average temperature · ${moneylessNumber(data.previousHumidity)}% humidity`;
        setMessage("Ward context loaded. Enter this week's weather.");
    } catch (error) {
        setMessage(error.message, true);
    }
}

city.addEventListener("change", populateWards);
ward.addEventListener("change", loadDefaults);

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    result.hidden = true;
    predictButton.disabled = true;
    predictButton.textContent = "Running CatBoost…";
    setMessage("Building live features and running the trained model…");

    const payload = {
        city: city.value,
        ward: ward.value,
        observationDate: observationDate.value,
        horizonWeeks: Number(document.getElementById("horizon").value),
        avgTempC: Number(document.getElementById("avgTemp").value),
        maxTempC: Number(document.getElementById("maxTemp").value),
        minTempC: Number(document.getElementById("minTemp").value),
        rainfallMm: Number(document.getElementById("rainfall").value),
        humidityPct: Number(document.getElementById("humidity").value),
    };

    try {
        const response = await fetch("/api/live-predict", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken,
                "Accept": "application/json",
            },
            credentials: "same-origin",
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Live prediction failed.");

        document.getElementById("predictedCases").textContent = moneylessNumber(data.predictedCases, 0);
        document.getElementById("targetDate").textContent = formatDate(data.targetDate);
        document.getElementById("targetHorizon").textContent = `${data.horizonWeeks} weeks`;
        document.getElementById("modelName").textContent = `${data.model} · ${data.modelTreeCount} trees`;
        riskBadge.textContent = data.riskCategory;
        riskBadge.dataset.risk = data.riskCategory;
        alertBox.textContent = data.warningMessage;
        alertBox.dataset.alert = String(data.earlyWarning);

        const inputSummary = document.getElementById("inputSummary");
        const inputs = [
            ["Observation date", formatDate(data.observationDate)],
            ["Average temperature", `${moneylessNumber(data.liveInput.avgTempC)} °C`],
            ["Maximum temperature", `${moneylessNumber(data.liveInput.maxTempC)} °C`],
            ["Minimum temperature", `${moneylessNumber(data.liveInput.minTempC)} °C`],
            ["Rainfall", `${moneylessNumber(data.liveInput.rainfallMm)} mm`],
            ["Humidity", `${moneylessNumber(data.liveInput.humidityPct)}%`],
            ["Current dengue cases input", "Not required — forecast uses prior confirmed case history"],
        ];
        inputSummary.innerHTML = inputs.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");

        document.getElementById("featureCount").textContent = data.modelInputFeatureCount;
        document.getElementById("artifactInfo").textContent = `Run: ${data.experimentId} · training data through ${formatDate(data.modelTrainingDataThrough)}.`;
        result.hidden = false;
        result.scrollIntoView({ behavior: "smooth", block: "start" });
        await loadOperations(city.value, scenarioControlsInitialized);
        setMessage(
            data.observationUpdated
                ? "This week's observation and forecast were updated successfully."
                : "Live early warning generated successfully."
        );
    } catch (error) {
        setMessage(error.message, true);
    } finally {
        predictButton.disabled = false;
        predictButton.textContent = "Run live early warning →";
    }
});
