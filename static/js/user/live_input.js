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

const areas = JSON.parse(document.body.dataset.areas || "{}");

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
        dateHelp.textContent = `Latest stored observation: ${formatDate(data.latestHistoricalDate)}. The live row must use ${formatDate(data.nextExpectedWeeklyDate)}.`;
        previousContext.hidden = false;
        previousContext.innerHTML = `Previous week: <strong>${moneylessNumber(data.previousCases, 0)} cases</strong> · ${moneylessNumber(data.previousRainfall)} mm rain · ${moneylessNumber(data.previousTemperature)} °C average temperature · ${moneylessNumber(data.previousHumidity)}% humidity`;
        setMessage("Ward context loaded. Enter this week's observations.");
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
        dengueCases: Number(document.getElementById("dengueCases").value),
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
            ["Current dengue cases", moneylessNumber(data.liveInput.dengueCases, 0)],
        ];
        inputSummary.innerHTML = inputs.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");

        document.getElementById("featureCount").textContent = data.modelInputFeatureCount;
        document.getElementById("artifactInfo").textContent = `Run: ${data.experimentId} · training data through ${formatDate(data.modelTrainingDataThrough)}.`;
        result.hidden = false;
        result.scrollIntoView({ behavior: "smooth", block: "start" });

        renderOperations(data.operations);
        setMessage("Live early warning generated successfully.");
    } catch (error) {
        setMessage(error.message, true);
    } finally {
        predictButton.disabled = false;
        predictButton.textContent = "Run live early warning →";
    }
});

function renderOperations(ops) {
    const section = document.getElementById("operations");
    if (!ops) {
        section.hidden = true;
        return;
    }
    section.hidden = false;

    const roiBadge = document.getElementById("roiBadge");
    roiBadge.textContent = ops.roi.rank;
    roiBadge.dataset.rank = ops.roi.rank;

    document.getElementById("opsDisclaimer").textContent = ops.disclaimer;

    const saline = ops.supply.salineBags;
    const kits = ops.supply.diagnosticKits;
    document.getElementById("supplySaline").innerHTML = supplyRowHtml(
        "Saline bags", saline.needed, saline.inStock, saline.remaining, saline.status
    );
    document.getElementById("supplyKits").innerHTML = supplyRowHtml(
        "Diagnostic kits", kits.needed, kits.inStock, kits.remaining, kits.status
    );

    const controls = document.getElementById("scarcityControls");
    controls.innerHTML = (ops.supply.controls || [])
        .map((c) => `<li>${c}</li>`).join("") || "<li class='muted'>No scarcity controls needed.</li>";

    const beds = ops.bedAllocation;
    document.getElementById("bedStats").innerHTML = `
        <div class="stat-line"><span>Peak beds needed</span><strong>${beds.peakBedsNeeded}</strong></div>
        <div class="stat-line"><span>Ward bed capacity</span><strong>${beds.wardBedCapacity}</strong></div>
        <div class="stat-line"><span>Allocated locally</span><strong>${beds.allocatedLocally}</strong></div>
        <div class="stat-line"><span>Overflow</span><strong>${beds.overflow}</strong></div>
        <div class="stat-line"><span>Unallocated</span><strong>${beds.unallocated}</strong></div>
    `;

    const routing = document.getElementById("overflowRouting");
    if (beds.overflowRouting && beds.overflowRouting.length > 0) {
        routing.innerHTML = `
            <p class="routing-title">Same-city overflow routing</p>
            ${beds.overflowRouting.map((r) => `
                <div class="routing-row"><span>→ ${r.toWard}</span><strong>${r.beds} beds</strong></div>
            `).join("")}
        `;
    } else if (beds.overflow > 0) {
        routing.innerHTML = `<p class="routing-title warning">Overflow cannot be routed within the city — consider inter-city transfer.</p>`;
    } else {
        routing.innerHTML = `<p class="routing-title muted">No overflow needed.</p>`;
    }

    document.getElementById("cityUtilisation").innerHTML = `
        <p class="utilisation-title">City-wide bed utilisation</p>
        <div class="utilisation-bar">
            <div class="utilisation-fill" style="width:${Math.min(100, beds.cityWideUtilisation * 100)}%"></div>
        </div>
        <p class="utilisation-label">${moneylessNumber(beds.cityWideUtilisation * 100, 0)}% of ${beds.totalCityBeds} beds in ${beds.city}</p>
    `;

    const crews = ops.crewDeployment;
    document.getElementById("crewStats").innerHTML = `
        <div class="stat-line"><span>Field crews</span><strong>${crews.fieldCrews}</strong></div>
        <div class="stat-line"><span>Deployment days</span><strong>${crews.deploymentDays}</strong></div>
        <div class="stat-line"><span>Estimated cost</span><strong>${moneylessNumber(crews.costEstimate, 0)}</strong></div>
        <div class="stat-line"><span>Priority tier</span><strong>${crews.priority}/4</strong></div>
    `;

    const cf = ops.counterfactual;
    document.getElementById("counterfactualStats").innerHTML = `
        <div class="stat-line"><span>No-intervention cases</span><strong>${moneylessNumber(cf.noInterventionCases, 0)}</strong></div>
        <div class="stat-line"><span>With-intervention cases</span><strong>${moneylessNumber(cf.interventionCases, 0)}</strong></div>
        <div class="stat-line"><span>Cases averted</span><strong>${moneylessNumber(cf.casesAverted, 0)}</strong></div>
        <p class="assumption-note">${cf.assumption}</p>
    `;

    const roi = ops.roi;
    document.getElementById("roiStats").innerHTML = `
        <div class="stat-line"><span>Total intervention cost</span><strong>${moneylessNumber(roi.totalCost, 0)}</strong></div>
        <div class="stat-line"><span>Est. cases averted</span><strong>${moneylessNumber(roi.estimatedCasesAverted, 0)}</strong></div>
        <div class="stat-line"><span>Value of averted burden</span><strong>${moneylessNumber(roi.valueAverted, 0)}</strong></div>
        <div class="stat-line"><span>ROI</span><strong>${roi.roiLabel} (${moneylessNumber(roi.roi, 2)}x)</strong></div>
    `;
}

function supplyRowHtml(label, needed, stock, remaining, status) {
    return `
        <div class="supply-head"><span>${label}</span><span class="status-chip" data-status="${status}">${status}</span></div>
        <div class="supply-bars">
            <div class="supply-bar"><span>Needed</span><strong>${moneylessNumber(needed, 0)}</strong></div>
            <div class="supply-bar"><span>In stock</span><strong>${moneylessNumber(stock, 0)}</strong></div>
            <div class="supply-bar"><span>Remaining</span><strong>${moneylessNumber(remaining, 0)}</strong></div>
        </div>
    `;
}
