/* AEDISIGHT hospital dashboard: selectors, forecast request, result + chart rendering. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";

  // ---------- mobile navigation ----------
  const sidebar = $("sidebar"), backdrop = $("sidebarBackdrop"), menuBtn = $("mobileMenu");
  function setNav(open) {
    sidebar.classList.toggle("open", open);
    backdrop.classList.toggle("is-visible", open);
    document.body.classList.toggle("nav-open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
    backdrop.tabIndex = open ? 0 : -1;
  }
  menuBtn.addEventListener("click", () => setNav(!sidebar.classList.contains("open")));
  backdrop.addEventListener("click", () => setNav(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setNav(false); });
  sidebar.querySelectorAll(".side-nav a").forEach((a) => a.addEventListener("click", () => setNav(false)));

  // ---------- city -> ward ----------
  const citySel = $("citySelect"), wardSel = $("wardSelect");
  citySel.addEventListener("change", () => {
    const opt = citySel.selectedOptions[0];
    wardSel.innerHTML = "";
    if (!opt || !opt.value) {
      wardSel.disabled = true;
      wardSel.add(new Option("Select a city first", ""));
      return;
    }
    const prefix = opt.dataset.wardPrefix, count = Number(opt.dataset.wardCount);
    wardSel.add(new Option("Select a ward", ""));
    for (let i = 1; i <= count; i++) {
      const n = String(i).padStart(2, "0");
      wardSel.add(new Option(`Ward ${n}`, `${prefix}_Ward_${n}`)); // value = ward_id used by the model
    }
    wardSel.disabled = false;
    setMessage("Now choose a ward.");
  });

  // ---------- helpers ----------
  const form = $("forecastForm"), btn = $("generateForecast");
  const panels = { empty: $("forecastEmpty"), loading: $("forecastLoading"), error: $("forecastError"), success: $("forecastSuccess") };
  function show(name) { Object.entries(panels).forEach(([k, el]) => { el.hidden = k !== name; }); }
  function setMessage(t) { $("forecastMessage").textContent = t; }
  function setBusy(busy) {
    btn.disabled = busy;
    $("buttonSpinner").hidden = !busy;
    $("buttonLabel").textContent = busy ? "Generating…" : "Generate forecast";
    document.getElementById("forecast-results").setAttribute("aria-busy", String(busy));
  }
  const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  // ---------- submit ----------
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const city = citySel.value, ward = wardSel.value;
    const horizon = Number(form.querySelector('input[name="horizon"]:checked').value);
    const asOf = $("asOfDate").value || null;
    if (!city || !ward) { setMessage("Please select both a city and a ward."); return; }

    setBusy(true); show("loading"); $("resultBadge").textContent = "Running…";
    try {
      const res = await fetch("/api/predict", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
        body: JSON.stringify({ city, ward, horizonWeeks: horizon, asOf }),
      });
      let data = {};
      try { data = await res.json(); } catch (_) { /* non-JSON error */ }
      if (!res.ok) {
        const d = data.detail;
        throw new Error(typeof d === "string" ? d : "Request failed (" + res.status + "). Please try again.");
      }
      render(data);
      setMessage("Forecast ready.");
    } catch (err) {
      $("forecastErrorMessage").textContent = err.message || "Forecast unavailable.";
      show("error"); $("resultBadge").textContent = "Unavailable";
      setMessage("Forecast could not be generated.");
    } finally { setBusy(false); }
  });

  // ---------- render ----------
  function render(d) {
    show("success");
    $("resultBadge").textContent = `${d.horizonWeeks}-week forecast`;
    $("predictedCases").textContent = Number(d.predictedCases).toFixed(1);
    $("predictionPeriod").textContent = `Expected weekly cases · week of ${fmtDate(d.targetDate)}`;
    const rc = $("riskCategory"); rc.textContent = d.riskCategory; rc.dataset.risk = d.riskCategory;
    const t = d.riskThresholds || {};
    $("riskDetail").textContent = `Low ≤ ${t.low_max?.toFixed(0)} · Medium ≤ ${t.medium_max?.toFixed(0)} · High ≤ ${t.high_max?.toFixed(0)} cases/wk`;
    $("observationDate").textContent = `Forecast origin: ${fmtDate(d.observationDate)} · dataset ends ${fmtDate(d.dataThrough)} · ${d.model}`;

    const an = $("actualNote");
    if (d.actualCases !== null && d.actualCases !== undefined) {
      an.hidden = false;
      an.textContent = `Backtest: ${d.actualCases} cases were actually recorded that week (forecast ${Number(d.predictedCases).toFixed(1)}).`;
    } else an.hidden = true;
    const sn = $("seasonNote");
    sn.hidden = !d.seasonNote; sn.textContent = d.seasonNote || "";
    $("riskWarning").textContent = d.riskWarning || "";

    $("driversEmpty").hidden = true; $("driverList").hidden = false;
    $("rainfallDriver").textContent = d.drivers.rainfall;
    $("temperatureDriver").textContent = d.drivers.temperature;
    $("humidityDriver").textContent = d.drivers.humidity;
    $("recentCasesDriver").textContent = d.drivers.recentCases;

    drawChart(d);
  }

  // ---------- chart ----------
  function drawChart(d) {
    const W = 760, H = 280, L = 44, R = 20, T = 16, B = 24;
    const hist = d.history.map((p) => ({ t: +new Date(p.date), v: p.cases }));
    const fc = d.forecast[0];
    const fcPt = { t: +new Date(fc.date), v: fc.cases };
    const last = hist[hist.length - 1];
    const hasActual = d.actualCases !== null && d.actualCases !== undefined;
    const maxV = Math.max(1, ...hist.map((p) => p.v), fcPt.v, hasActual ? d.actualCases : 0) * 1.15;
    const t0 = hist[0].t, t1 = fcPt.t;
    const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
    const y = (v) => H - B - (v / maxV) * (H - T - B);

    const grid = $("chartGrid"), axis = $("chartYAxis");
    grid.innerHTML = ""; axis.innerHTML = "";
    for (let i = 0; i <= 4; i++) {
      const v = (maxV / 4) * i, yy = y(v);
      const ln = document.createElementNS(NS, "line");
      ln.setAttribute("x1", L); ln.setAttribute("x2", W - R); ln.setAttribute("y1", yy); ln.setAttribute("y2", yy);
      grid.appendChild(ln);
      const tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", L - 8); tx.setAttribute("y", yy + 4); tx.setAttribute("text-anchor", "end");
      tx.textContent = v.toFixed(v >= 10 ? 0 : 1);
      axis.appendChild(tx);
    }
    $("historyLine").setAttribute("points", hist.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" "));
    $("forecastLine").setAttribute("points", `${x(last.t).toFixed(1)},${y(last.v).toFixed(1)} ${x(fcPt.t).toFixed(1)},${y(fcPt.v).toFixed(1)}`);
    const div = $("forecastDivider");
    div.setAttribute("x1", x(last.t)); div.setAttribute("x2", x(last.t)); div.setAttribute("y1", T); div.setAttribute("y2", H - B);

    const old = document.getElementById("actualDot"); if (old) old.remove();
    if (hasActual) {
      const c = document.createElementNS(NS, "circle");
      c.id = "actualDot"; c.setAttribute("class", "actual-dot"); c.setAttribute("r", 5);
      c.setAttribute("cx", x(fcPt.t)); c.setAttribute("cy", y(d.actualCases));
      $("forecastChart").appendChild(c);
    }
    $("chartSvgDescription").textContent =
      `Last 12 weekly case counts up to ${d.observationDate}, then a forecast of ${fc.cases} cases for ${fc.date}` +
      (hasActual ? `; actual recorded cases were ${d.actualCases}.` : ".");
    $("chartStartDate").textContent = fmtDate(d.history[0].date);
    $("chartObservedDate").textContent = "Origin " + fmtDate(d.observationDate);
    $("chartForecastDate").textContent = "Forecast " + fmtDate(fc.date);
    $("chartEmpty").hidden = true; $("chartContent").hidden = false;
  }
})();
