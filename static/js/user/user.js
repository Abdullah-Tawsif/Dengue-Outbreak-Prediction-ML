"use strict";

const sidebar = document.getElementById("sidebar");
const menuButton = document.getElementById("mobileMenu");
const backdrop = document.getElementById("sidebarBackdrop");
const mobileViewport = window.matchMedia("(max-width: 900px)");

function setMenuOpen(isOpen, returnFocus = false) {
    const shouldOpen = isOpen && mobileViewport.matches;

    sidebar.classList.toggle("open", shouldOpen);
    backdrop.classList.toggle("is-visible", shouldOpen);
    menuButton.setAttribute("aria-expanded", String(shouldOpen));
    menuButton.setAttribute(
        "aria-label",
        shouldOpen ? "Close navigation menu" : "Open navigation menu"
    );
    document.body.classList.toggle("nav-open", shouldOpen);

    if (shouldOpen) {
        sidebar.querySelector("a")?.focus();
    } else if (returnFocus) {
        menuButton.focus();
    }
}

menuButton.addEventListener("click", () => {
    setMenuOpen(menuButton.getAttribute("aria-expanded") !== "true");
});
backdrop.addEventListener("click", () => setMenuOpen(false));

sidebar.querySelectorAll("nav a").forEach((link) => {
    link.addEventListener("click", () => {
        sidebar.querySelectorAll("nav a").forEach((item) => {
            item.classList.remove("active");
            item.removeAttribute("aria-current");
        });

        link.classList.add("active");
        link.setAttribute("aria-current", "location");
        setMenuOpen(false);
    });
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menuButton.getAttribute("aria-expanded") === "true") {
        setMenuOpen(false, true);
    }
});

mobileViewport.addEventListener("change", (event) => {
    if (!event.matches) {
        setMenuOpen(false);
    }
});

const forecastForm = document.getElementById("forecastForm");
const citySelect = document.getElementById("citySelect");
const wardSelect = document.getElementById("wardSelect");
const generateButton = document.getElementById("generateForecast");
const buttonLabel = document.getElementById("buttonLabel");
const buttonSpinner = document.getElementById("buttonSpinner");
const forecastResults = document.getElementById("forecast-results");
const forecastMessage = document.getElementById("forecastMessage");
const resultBadge = document.getElementById("resultBadge");
const forecastEmpty = document.getElementById("forecastEmpty");
const forecastLoading = document.getElementById("forecastLoading");
const forecastError = document.getElementById("forecastError");
const forecastErrorMessage = document.getElementById("forecastErrorMessage");
const forecastSuccess = document.getElementById("forecastSuccess");
const driversEmpty = document.getElementById("driversEmpty");
const driverList = document.getElementById("driverList");
const chartEmpty = document.getElementById("chartEmpty");
const chartContent = document.getElementById("chartContent");

function populateWards() {
    const cityOption = citySelect.selectedOptions[0];
    const wardPrefix = cityOption.dataset.wardPrefix;
    const wardCount = Number(cityOption.dataset.wardCount || 0);

    wardSelect.replaceChildren(new Option(
        wardCount ? "Select a ward" : "Select a city first",
        ""
    ));
    wardSelect.disabled = !wardCount;

    for (let wardNumber = 1; wardNumber <= wardCount; wardNumber += 1) {
        const suffix = String(wardNumber).padStart(2, "0");
        wardSelect.add(new Option(
            `Ward ${suffix}`,
            `${wardPrefix}_Ward_${suffix}`
        ));
    }
}

function setForecastState(state, message = "") {
    const isLoading = state === "loading";
    const horizonInputs = forecastForm.querySelectorAll('input[name="horizon"]');

    forecastResults.setAttribute("aria-busy", String(isLoading));
    forecastEmpty.hidden = state !== "empty";
    forecastLoading.hidden = !isLoading;
    forecastError.hidden = state !== "error";
    forecastSuccess.hidden = state !== "success";
    driversEmpty.hidden = state === "success";
    driverList.hidden = state !== "success";
    chartEmpty.hidden = state === "success";
    chartContent.hidden = state !== "success";
    generateButton.disabled = isLoading;
    citySelect.disabled = isLoading;
    wardSelect.disabled = isLoading || !citySelect.value;
    horizonInputs.forEach((input) => {
        input.disabled = isLoading;
    });
    buttonSpinner.hidden = !isLoading;
    buttonLabel.textContent = isLoading ? "Preparing forecast" : "Generate forecast";

    if (state === "loading") {
        resultBadge.textContent = "Preparing";
        forecastMessage.textContent = "Preparing your selected forecast.";
    } else if (state === "error") {
        resultBadge.textContent = "Unavailable";
        forecastMessage.textContent = "No prediction was generated.";
        forecastErrorMessage.textContent = message;
    } else if (state === "success") {
        resultBadge.textContent = "Forecast ready";
        forecastMessage.textContent = "Forecast results are ready.";
    } else {
        resultBadge.textContent = "Awaiting forecast";
        forecastMessage.textContent = "Select a city and ward to prepare your forecast request.";
        forecastErrorMessage.textContent = "";
    }
}

function clearForecast() {
    setForecastState("empty");
}

citySelect.addEventListener("change", () => {
    populateWards();
    clearForecast();
});

wardSelect.addEventListener("change", clearForecast);
forecastForm.querySelectorAll('input[name="horizon"]').forEach((input) => {
    input.addEventListener("change", clearForecast);
});

function formatDate(value) {
    if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
        throw new Error("The forecast service returned an invalid observation date.");
    }

    return new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
    }).format(new Date(value));
}

function validateSeries(series, name, minimumLength) {
    if (
        !Array.isArray(series)
        || series.length < minimumLength
        || series.some((point) => (
            !point
            || typeof point.date !== "string"
            || Number.isNaN(Date.parse(point.date))
            || !Number.isFinite(point.cases)
            || point.cases < 0
        ))
    ) {
        throw new Error(`The forecast service returned invalid ${name} data.`);
    }

    return series;
}

function drawForecastChart(history, forecast, selection) {
    const svgNamespace = "http://www.w3.org/2000/svg";
    const chartGrid = document.getElementById("chartGrid");
    const chartYAxis = document.getElementById("chartYAxis");
    const historyLine = document.getElementById("historyLine");
    const forecastLine = document.getElementById("forecastLine");
    const divider = document.getElementById("forecastDivider");
    const chartDescription = document.getElementById("chartSvgDescription");
    const chartTitle = document.getElementById("chartSvgTitle");
    const allValues = [...history, ...forecast].map((point) => point.cases);
    const maxCases = Math.max(1, ...allValues);
    const plot = { left: 58, right: 736, top: 20, bottom: 236 };
    const totalPoints = history.length + forecast.length;

    chartGrid.replaceChildren();
    chartYAxis.replaceChildren();

    for (let line = 0; line < 5; line += 1) {
        const y = plot.top + ((plot.bottom - plot.top) * line) / 4;
        const gridLine = document.createElementNS(svgNamespace, "line");
        gridLine.setAttribute("x1", String(plot.left));
        gridLine.setAttribute("x2", String(plot.right));
        gridLine.setAttribute("y1", String(y));
        gridLine.setAttribute("y2", String(y));
        chartGrid.append(gridLine);

        const axisLabel = document.createElementNS(svgNamespace, "text");
        axisLabel.setAttribute("x", String(plot.left - 10));
        axisLabel.setAttribute("y", String(y + 4));
        axisLabel.setAttribute("text-anchor", "end");
        axisLabel.textContent = new Intl.NumberFormat(undefined, {
            maximumFractionDigits: 1,
        }).format(maxCases * (4 - line) / 4);
        chartYAxis.append(axisLabel);
    }

    function pointCoordinates(point, index) {
        const x = plot.left + ((plot.right - plot.left) * index) / (totalPoints - 1);
        const y = plot.bottom - ((plot.bottom - plot.top) * point.cases) / maxCases;
        return `${x},${y}`;
    }

    historyLine.setAttribute(
        "points",
        history.map(pointCoordinates).join(" ")
    );
    forecastLine.setAttribute(
        "points",
        [history[history.length - 1], ...forecast]
            .map((point, index) => pointCoordinates(
                point,
                history.length - 1 + index
            ))
            .join(" ")
    );

    const dividerX = plot.left
        + ((plot.right - plot.left) * (history.length - 1)) / (totalPoints - 1);
    divider.setAttribute("x1", String(dividerX));
    divider.setAttribute("x2", String(dividerX));
    divider.setAttribute("y1", String(plot.top));
    divider.setAttribute("y2", String(plot.bottom));

    chartTitle.textContent = `${selection.city} ${wardSelect.selectedOptions[0].textContent}: historical cases and forecast`;
    chartDescription.textContent = `Observed dengue cases through ${formatDate(history.at(-1).date)}, followed by the ${selection.horizonWeeks}-week forecast.`;
    document.getElementById("chartStartDate").textContent = formatDate(history[0].date);
    document.getElementById("chartObservedDate").textContent = formatDate(history.at(-1).date);
    document.getElementById("chartForecastDate").textContent = formatDate(forecast.at(-1).date);
}

/*
Expected forecast-service response:
predictedCases, riskCategory, observationDate, drivers (rainfall, temperature,
humidity, recentCases), history, and forecast date/cases series.
*/
function renderForecastResult(result, selection) {
    if (
        !result
        || !Number.isFinite(result.predictedCases)
        || result.predictedCases < 0
        || typeof result.riskCategory !== "string"
        || !result.riskCategory.trim()
        || !result.drivers
    ) {
        throw new Error("The forecast service returned an incomplete result.");
    }

    const history = validateSeries(result.history, "historical case", 2);
    const forecast = validateSeries(result.forecast, "forecast", 1);
    const drivers = [
        ["rainfallDriver", result.drivers.rainfall],
        ["temperatureDriver", result.drivers.temperature],
        ["humidityDriver", result.drivers.humidity],
        ["recentCasesDriver", result.drivers.recentCases],
    ];

    if (drivers.some(([, value]) => typeof value !== "string" || !value.trim())) {
        throw new Error("The forecast service returned incomplete driver explanations.");
    }

    document.getElementById("predictedCases").textContent =
        new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
            .format(result.predictedCases);
    document.getElementById("riskCategory").textContent = result.riskCategory;
    document.getElementById("predictionPeriod").textContent =
        `${selection.horizonWeeks}-week forecast`;
    document.getElementById("observationDate").textContent =
        `Based on observations through ${formatDate(result.observationDate)}.`;

    drivers.forEach(([elementId, value]) => {
        document.getElementById(elementId).textContent = value;
    });

    drawForecastChart(history, forecast, selection);
    setForecastState("success");
}

async function requestForecast(selection) {
    const service = window.aedisightForecastService;

    if (!service || typeof service.generate !== "function") {
        throw new Error(
            "The model API is not connected yet. No prediction was generated."
        );
    }

    return service.generate(selection);
}

forecastForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!forecastForm.reportValidity()) {
        return;
    }

    const selection = {
        city: citySelect.value,
        ward: wardSelect.value,
        horizonWeeks: Number(
            forecastForm.querySelector('input[name="horizon"]:checked').value
        ),
    };

    setForecastState("loading");

    try {
        const result = await requestForecast(selection);
        renderForecastResult(result, selection);
    } catch (error) {
        const message = error instanceof Error
            ? error.message
            : "The forecast could not be generated. No prediction was returned.";
        setForecastState("error", message);
    }
});
