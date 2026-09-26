/* FlowGuard frontend: replay + panels + Scenario Lab, all client-side over one /api/day payload.
   The scenario maths mirrors flowguard/pipeline/fg_core.py (build_timeline / _action): with no scenario
   active it reproduces the server's gap, readiness and actions exactly. */
"use strict";

const $ = (s) => document.querySelector(s);
const SLOT_MIN = 30;
const HORIZONS = [1, 2, 3, 4];
const SEVERE = new Set(["Strained", "Critical"]);
const CHART_FROM = 12; // radar x-axis starts at 06:00
const DAY_NAMES = { Mon: "Monday", Tue: "Tuesday", Wed: "Wednesday", Thu: "Thursday", Fri: "Friday", Sat: "Saturday", Sun: "Sunday" };

const ICONS = {
  check: '<svg viewBox="0 0 24 24"><path d="M9.5 16.2 5.3 12l-1.4 1.4 5.6 5.6 11-11-1.4-1.4z"/></svg>',
  bang: '<svg viewBox="0 0 24 24"><path d="M11 4h2v11h-2zM11 17h2v3h-2z"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M12 4 3 20h18z"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6.4 5 5 6.4 10.6 12 5 17.6 6.4 19l5.6-5.6 5.6 5.6 1.4-1.4-5.6-5.6L19 6.4 17.6 5 12 10.6z"/></svg>',
  dash: '<svg viewBox="0 0 24 24"><path d="M5 11h14v2H5z"/></svg>',
};
const STATUS = {
  Prepared: ["st-good", "check"], Watch: ["st-warning", "bang"], Strained: ["st-serious", "up"], Critical: ["st-critical", "x"],
  "No service": ["st-none", "dash"],
  Normal: ["st-good", "check"], Elevated: ["st-warning", "bang"], High: ["st-serious", "up"], Severe: ["st-critical", "x"],
};

const state = {
  pois: [], poi: null, days: null, model: null, day: null, date: null, slot: 26, playing: false, speed: 1, timer: null, source: "",
  sc: { crowd: 0, service: 0, eventSlot: 44, eventSize: 0 },
};
let radar = null;

// ---------------------------------------------------------------- helpers
const fmtSlot = (i) => {
  const m = i * SLOT_MIN;
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
};
const slotOf = (hhmm) => Number(hhmm.slice(0, 2)) * 2 + Math.floor(Number(hhmm.slice(3, 5)) / SLOT_MIN);
const fmtMin = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(((m % 60) + 60) % 60).padStart(2, "0")}`;
const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const x2 = (v) => (v == null ? "–" : `${v.toFixed(v >= 10 ? 0 : 1)}×`);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function fmtDate(d, opts = { weekday: "short", day: "numeric", month: "short", year: "numeric" }) {
  const [y, m, dd] = d.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, dd)).toLocaleDateString("en-CA", { ...opts, timeZone: "UTC" });
}
function levelOf(v, levels) {
  if (v == null || !isFinite(v)) return null;
  for (const [upper, name] of levels) if (upper === null || v < upper) return name;
  return levels[levels.length - 1][1];
}
function chip(level, extra = "") {
  if (!level) return '<span class="chip st-none"><span class="dot"></span>–</span>';
  const [cls, icon] = STATUS[level] || ["st-none", "dash"];
  return `<span class="chip ${cls}"><span class="dot">${ICONS[icon]}</span>${esc(level)}${extra}</span>`;
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

// ---------------------------------------------------------------- scenario maths
const scenarioActive = () => state.sc.crowd !== 0 || state.sc.service !== 0 || state.sc.eventSize > 0;

function extraTotal(tau) {
  const k = tau - state.sc.eventSlot;
  if (state.sc.eventSize <= 0 || k < 0 || k > 2) return 0;
  return state.sc.eventSize * state.day.exit.normal_peak * [0.6, 0.3, 0.1][k];
}

/** Readiness for one route group at replay slot t, horizons +30…+120, with the scenario applied. */
function view(g, t) {
  const day = state.day, G = day.groups[g], r = G.rows[t], th = day.thresholds, sc = state.sc;
  return HORIZONS.map((h, k) => {
    const tau = t + h, base = r.dep[k];
    if (base == null || r.svc[k] == null) return { tau, ready: null };
    const dem = base * (1 + sc.crowd / 100) + extraTotal(tau) * day.group_mix[g];
    const svc = r.svc[k] * (1 + sc.service / 100);
    const idx = r.idx[k] != null && base > 0 ? (r.idx[k] * dem) / base : null;
    let gap = null, ready;
    if (r.svc[k] <= 0) ready = "No service";
    else {
      // scale the server's gap (exact when no scenario; handles targets past midnight on another day type)
      gap = r.gap[k] != null && base > 0
        ? (r.gap[k] * (dem / base) * Math.max(r.svc[k], 0.5)) / Math.max(svc, 0.5)
        : dem / Math.max(svc, 0.5) / G.typical_load;
      ready = dem < th.min_demand_share * G.typical_demand ? "Prepared" : levelOf(gap, th.readiness);
    }
    return { tau, dem, svc, idx, gap, ready };
  });
}

function actionsAt(t) {
  const out = [];
  for (const g of state.day.group_order) {
    const v = view(g, t), lv = v.map((x) => x.ready);
    const first = lv.findIndex((l) => SEVERE.has(l));
    if (first < 0) continue;
    let last = first;
    while (last + 1 < lv.length && SEVERE.has(lv[last + 1])) last++;
    const span = v.slice(first, last + 1);
    const start = t + HORIZONS[first], end = t + HORIZONS[last] + 1;
    out.push({
      g, peak: Math.max(...span.map((x) => x.gap)),
      worst: span.some((x) => x.ready === "Critical") ? "Critical" : "Strained",
      lead: HORIZONS[first] * SLOT_MIN, start, end, stage: fmtMin(start * SLOT_MIN - 15),
    });
  }
  return out.sort((a, b) => b.peak - a.peak);
}

// ---------------------------------------------------------------- renderers
function renderClock() {
  $("#clock").textContent = fmtSlot(state.slot);
  $("#clockDate").textContent = state.day ? fmtDate(state.day.date) : "";
  $("#scrub").value = state.slot;
}

function renderPressure() {
  const d = state.day, s = d.slots[state.slot];
  $("#pressureVal").textContent = s.pressure == null ? "–" : s.pressure.toFixed(1);
  $("#pressureChip").innerHTML = chip(s.level);
  $("#pressureCaption").textContent = `${d.future ? "Expected people" : "People"} on site vs a normal ${DAY_NAMES[d.day_type]} at ${s.t}`;

  const from = Math.min(CHART_FROM, state.slot), pts = d.slots.slice(from, state.slot + 1).map((x) => x.pressure ?? 0);
  const all = d.slots.map((x) => x.pressure ?? 0);
  const ymax = Math.max(2.5, ...all), ymin = 0.5, W = 320, H = 64, n = 47 - from;
  const X = (i) => (i / Math.max(n, 1)) * W, Y = (v) => H - ((Math.max(v, ymin) - ymin) / (ymax - ymin)) * H;
  const path = pts.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const lastX = X(pts.length - 1), lastY = Y(pts[pts.length - 1] ?? 0);
  $("#spark").innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <line x1="0" x2="${W}" y1="${Y(1)}" y2="${Y(1)}" stroke="${css("--axis")}" stroke-width="1.5" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
    <text x="${W}" y="${Y(1) - 4}" text-anchor="end" font-size="11" fill="${css("--muted")}">normal</text>
    <path d="${path}" fill="none" stroke="${css("--series-actual")}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    <circle cx="${lastX}" cy="${lastY}" r="4" fill="${css("--series-actual")}" stroke="${css("--surface")}" stroke-width="2"/>
  </svg>`;
}

function renderSignature() {
  const s = state.day.slots[state.slot];
  $("#sigLabel").textContent = s.signature || "–";
  const pp = s.regional != null && s.regional_base != null ? Math.round((s.regional - s.regional_base) * 100) : null;
  $("#sigInd").innerHTML = `
    <li title="Share of the last 2 h of arrivals from the South, East and rest-of-BC corridors"><span>Regional share</span><b>${pct(s.regional)} <small>${pp == null ? "" : `(${pp >= 0 ? "+" : "−"}${Math.abs(pp)} pp)`}</small></b></li>
    <li><span>Stay length vs normal</span><b>${x2(s.stay)}</b></li>
    <li><span>Out-of-province share vs normal</span><b>${x2(s.visitor)}</b></li>`;
}

function renderCatchment() {
  const d = state.day, s = d.slots[state.slot];
  $("#offNS").innerHTML = s.local == null ? "" :
    `<b>${pct(1 - s.local)}</b> ${esc(d.poi.catchment_kpi)} <span>(normal ${pct(1 - s.local_base)})</span>`;
  const max = Math.max(0.2, ...d.corridors.flatMap((c) => [s.shares[c.key] ?? 0, s.base_shares[c.key] ?? 0])) * 1.1;
  $("#catch").innerHTML = d.corridors.map((c) => {
    const v = s.shares[c.key], b = s.base_shares[c.key];
    return `<div class="catch-row" title="${esc(c.label)}: ${pct(v)} of arrivals in the last 2 h (normal ${pct(b)})">
      <span class="name">${esc(c.label)}</span>
      <div class="bar"><div class="fill" style="width:${((v ?? 0) / max) * 100}%"></div>
        <div class="norm" style="left:${((b ?? 0) / max) * 100}%"></div></div>
      <span class="val">${pct(v)} <small>vs ${pct(b)}</small></span></div>`;
  }).join("");
}

function renderAction() {
  const d = state.day, acts = actionsAt(state.slot), card = $("#actionCard");
  const tag = (d.future ? " <small>(outlook)</small>" : "") + (scenarioActive() ? " <small>(scenario)</small>" : "");
  if (!acts.length) {
    card.style.setProperty("--st", css("--good"));
    card.innerHTML = `<div class="action-top">${chip("Prepared")}
      <div class="action-title">All route groups prepared for the next 2 hours${tag}</div></div>
      <p class="action-why">Expected exit demand per unit of scheduled service is within the normal range for the ${esc(d.service_label)} schedule. No action needed.</p>`;
    return;
  }
  const a = acts[0], G = d.groups[a.g];
  card.style.setProperty("--st", css(a.worst === "Critical" ? "--critical" : "--serious"));
  const also = acts.slice(1).map((x) => `<div class="also-row">${chip(x.worst)}
    <span><b>${esc(d.groups[x.g].label)}</b> ${fmtSlot(x.start)}–${fmtSlot(x.end)} · in ${x.lead} min · ${x.peak.toFixed(1)}× normal load</span></div>`).join("");
  card.innerHTML = `
    <div class="action-top">${chip(a.worst)}
      <div class="action-title">${esc(G.label)} ${a.worst.toLowerCase()} ${fmtSlot(a.start)}–${fmtSlot(a.end)}${tag}</div>
      <div class="lead"><b>in ${a.lead} min</b><span>lead time</span></div></div>
    <p class="action-why">Expected ${esc(G.label.toLowerCase())} exit demand per unit of scheduled service is <b>${a.peak.toFixed(1)}×</b> a normal ${esc(d.service_label)}, on the ${esc(d.service_label)} schedule (${esc(G.routes)}).</p>
    <ul class="levers">${G.levers.map((l) => `<li>${esc(l.replace("{start}", a.stage))}</li>`).join("")}</ul>
    ${also ? `<div class="also">${also}</div>` : ""}`;
}

function radarData() {
  const d = state.day, t = state.slot, e = d.exit, f = 100 / e.normal_peak, sc = state.sc;
  const idx = [...Array(48 - CHART_FROM).keys()].map((i) => i + CHART_FROM);
  const now = d.future ? e.expected[t] : e.actual[t];
  const actual = idx.map((i) => (!d.future && i <= t ? e.actual[i] * f : null));
  const expected = idx.map((i) => (d.future && e.expected[i] != null ? e.expected[i] * f : null));
  const usual = idx.map((i) => (e.usual[i] == null ? null : e.usual[i] * f));
  const forecast = idx.map((i) => {
    if (i === t) return now == null ? null : now * f;
    const k = i - t;
    if (k < 1 || k > 4) return null;
    const v = e.forecast[t]?.[k - 1];
    return v == null ? null : (v * (1 + sc.crowd / 100) + extraTotal(i)) * f;
  });
  const earlier = idx.map((i) => (!d.future && i <= t && i >= 2 && e.forecast[i - 2]?.[1] != null ? e.forecast[i - 2][1] * f : null));
  return { labels: idx.map(fmtSlot), actual, expected, usual, forecast, earlier, nowIdx: t - CHART_FROM };
}

const nowLine = {
  id: "nowLine",
  afterDatasetsDraw(chart) {
    const i = chart.$now;
    if (i == null || i < 0) return;
    const { ctx, chartArea: a, scales: { x } } = chart;
    const px = x.getPixelForValue(i), end = x.getPixelForValue(Math.min(i + 4, x.max));
    ctx.save();
    ctx.fillStyle = css("--series-forecast");
    ctx.globalAlpha = 0.07;
    ctx.fillRect(px, a.top, end - px, a.bottom - a.top);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = css("--ink");
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = css("--ink");
    ctx.font = `700 13px ${css("--font")}`;
    ctx.textAlign = "center";
    ctx.fillText("NOW", px, a.top - 6);
    ctx.restore();
  },
};

function radarColors(chart) {
  const [a, f, e, u, x] = chart.data.datasets;
  x.borderColor = css("--outlook");
  a.borderColor = a.backgroundColor = css("--series-actual");
  f.borderColor = f.backgroundColor = css("--series-forecast");
  f.pointBorderColor = css("--surface");
  e.borderColor = css("--series-forecast");
  e.backgroundColor = css("--surface");
  u.borderColor = css("--series-usual");
  chart.options.scales.x.grid.color = chart.options.scales.y.grid.color = css("--grid");
  chart.options.scales.x.border.color = chart.options.scales.y.border.color = css("--axis");
  chart.options.scales.x.ticks.color = chart.options.scales.y.ticks.color = css("--muted");
  chart.options.scales.y.title.color = css("--muted");
}

function renderRadar() {
  const r = radarData();
  if (!radar) {
    Chart.defaults.font.family = css("--font");
    Chart.defaults.font.size = 13;
    radar = new Chart($("#radar"), {
      type: "line",
      data: {
        labels: r.labels,
        datasets: [
          { label: "Actual exits", data: r.actual, borderWidth: 2.5, pointRadius: 0, tension: 0.25, spanGaps: false },
          { label: "Forecast from now", data: r.forecast, borderWidth: 2.5, borderDash: [7, 5], pointRadius: 5, pointBorderWidth: 2, tension: 0.25 },
          { label: "Forecast made 60 min earlier", data: r.earlier, showLine: false, pointRadius: 4, pointBorderWidth: 2 },
          { label: "Normal day", data: r.usual, borderWidth: 2, pointRadius: 0, tension: 0.25 },
          { label: "Expected exits (outlook)", data: r.expected, borderWidth: 2.5, borderDash: [3, 4], pointRadius: 0, tension: 0.25 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
        layout: { padding: { top: 18 } },
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => items[0]?.label,
              label: (c) => (c.raw == null ? null : `${c.dataset.label}: ${Math.round(c.raw)}`),
            },
          },
        },
        scales: {
          x: { grid: { display: false }, border: {}, ticks: { autoSkip: false, maxRotation: 0, callback(v) { const l = this.getLabelForValue(v); return l.endsWith(":00") && Number(l.slice(0, 2)) % 2 === 0 ? l : ""; } } },
          y: { beginAtZero: true, grid: {}, border: { display: false }, ticks: { maxTicksLimit: 6 }, title: { display: true, text: "Exit index (normal peak = 100)" } },
        },
      },
      plugins: [nowLine],
    });
    radarColors(radar);
  }
  const ds = radar.data.datasets;
  radar.data.labels = r.labels;
  ds[0].data = r.actual; ds[1].data = r.forecast; ds[2].data = r.earlier; ds[3].data = r.usual; ds[4].data = r.expected;
  radar.$now = r.nowIdx;
  radar.update();
}

function renderReady() {
  const d = state.day, t = state.slot;
  const head = `<thead><tr><th>Route group</th><th>Exit demand<br>+60 min</th><th title="Scheduled departures per 30 min in bus-equivalents (SkyTrain / SeaBus ≈ 5 buses, West Coast Express ≈ 12)">Scheduled service<br>/ 30 min</th>
    ${HORIZONS.map((h) => `<th>+${h * SLOT_MIN} min<br><span style="text-transform:none">${fmtSlot(t + h)}</span></th>`).join("")}</tr></thead>`;
  const rows = d.group_order.map((g) => {
    const G = d.groups[g], v = view(g, t), mid = v[1];
    return `<tr><td class="grp"><b>${esc(G.label)}</b><small>${esc(G.routes)}</small></td>
      <td class="num">${x2(mid.idx)} <small>normal</small></td>
      <td class="num">${mid.svc == null ? "–" : mid.svc.toFixed(1)}</td>
      ${v.map((x) => `<td><div class="cell">${chip(x.ready)}<span class="gap">${x.gap == null ? "&nbsp;" : `${x.gap.toFixed(1)}× normal load`}</span></div></td>`).join("")}</tr>`;
  }).join("");
  $("#ready").innerHTML = head + `<tbody>${rows}</tbody>`;
}

function renderBanner() {
  const b = $("#banner"), sc = state.sc, parts = [];
  if (sc.crowd) parts.push(`crowd ${sc.crowd > 0 ? "+" : "−"}${Math.abs(sc.crowd)}%`);
  if (sc.service) parts.push(`scheduled service ${sc.service > 0 ? "+" : "−"}${Math.abs(sc.service)}%`);
  if (sc.eventSize > 0) parts.push(`event lets out at ${fmtSlot(sc.eventSlot)}`);
  if (parts.length) {
    b.hidden = false; b.className = "banner scenario";
    b.innerHTML = `<b>Scenario active:</b> ${parts.join(" · ")}. Forecasts and readiness below include it. <button class="btn small" data-preset="reset">Reset</button>`;
  } else if (!b.classList.contains("error")) b.hidden = true;
}

function renderAll() {
  if (!state.day) return;
  renderClock(); renderPressure(); renderSignature(); renderCatchment();
  renderAction(); renderRadar(); renderReady(); renderBanner();
}

// ---------------------------------------------------------------- data loading
function showError(msg) {
  const b = $("#banner");
  b.hidden = false; b.className = "banner error"; b.textContent = msg;
}

function showLoader(text) {
  $("#loaderText").textContent = text;
  $("#loader").hidden = false;
  $("#main").classList.add("loading");
}
function hideLoader() {
  $("#loader").hidden = true;
  $("#main").classList.remove("loading");
}
const poiName = () => state.pois.find((p) => p.key === state.poi)?.name || "";

function renderOutlook() {
  const d = state.day, on = !!d.future;
  document.body.classList.toggle("outlook", on);
  $("#outlookBadge").hidden = !on;
  $("#outlookBar").hidden = !on;
  if (on) {
    $("#outlookBar").innerHTML = `<b>Outlook: expected conditions, not observed data.</b> ${esc(d.method || "")}.
      Scheduled service is TransLink's published timetable for ${fmtDate(d.date)}. Readiness and actions use the same model as past days.`;
  }
}

async function loadDay(date) {
  showLoader(`Loading ${poiName()} · ${fmtDate(date)}…`);
  try {
    const day = await getJSON(`/api/day?poi=${state.poi}&date=${date}`);
    state.day = day; state.date = date; state.source = day.source;
    $("#day").value = date;
    renderOutlook();
    $("#banner").classList.remove("error");
    $("#source").textContent = day.source === "live" ? "Data: live · Databricks SQL (gold tables)" : "Data: offline snapshot of the gold tables";
    syncUrl();
    renderAll();
  } catch (e) {
    showError(`Couldn't load ${fmtDate(date)}: ${e.message}`);
  } finally {
    hideLoader();
  }
}

function fillDays() {
  const opt = (d, prefix = "") => `<option value="${d.date}">${prefix}${fmtDate(d.date)}${d.label ? ` · ${esc(d.label)}` : ""}${d.surge_ratio == null ? "" : ` · ${d.surge_ratio.toFixed(2)}× usual`}</option>`;
  const D = state.days;
  const preset = new Set([...D.presets, ...(D.outlook_presets || [])].map((d) => d.date));
  const rest = (list) => list.filter((d) => !preset.has(d.date));
  const out = (d) => opt(d, "Outlook · ");
  $("#day").innerHTML = `<optgroup label="Demo days · history (in the data)">${D.presets.map((d) => opt(d)).join("")}</optgroup>
    ${(D.outlook_presets || []).length ? `<optgroup label="Demo days · outlook (future, expected)">${D.outlook_presets.map(out).join("")}</optgroup>` : ""}
    <optgroup label="History · Nov 2025 – Aug 2026 (observed)">${rest(D.days).map((d) => opt(d)).join("")}</optgroup>
    ${(D.future || []).length ? `<optgroup label="Outlook · Sep 7 2026 – Jan 3 2027 (expected, not observed)">${rest(D.future).map(out).join("")}</optgroup>` : ""}`;
}

function renderTrust() {
  const bt = state.model?.backtest || [];
  const get = (model, target, h) => bt.find((r) => r.model === model && r.target === target && r.horizon === h)?.r2;
  const f = (v) => (v == null ? "–" : v.toFixed(2));
  $("#trustBody").innerHTML = `
    <div>Exit forecast accuracy on unseen days (trained Nov–Jun, tested Jul–Aug 2026), R² = share of variation explained:</div>
    <table><tr><th></th>${HORIZONS.map((h) => `<th>+${h * SLOT_MIN} min</th>`).join("")}</tr>
      <tr><td><b>FlowGuard egress model</b></td>${HORIZONS.map((h) => `<td><b>${f(get("egress", "departures", h))}</b></td>`).join("")}</tr>
      <tr><td>Same model without today's busyness</td>${HORIZONS.map((h) => `<td>${f(get("egress_static", "departures", h))}</td>`).join("")}</tr>
      <tr><td>Typical-week pattern</td>${HORIZONS.map((h) => `<td>${f(get("typical_week", "departures", h))}</td>`).join("")}</tr></table>
    <div>Learned from dwell time: ${(state.model?.kernel || []).filter((k) => [1, 2, 4].includes(k.k)).map((k) => `${pct(k.cum_p)} of visitors have left within ${(k.k + 1) * 30} min of arriving`).join(" · ")}.</div>
    <div><b>Outlook days</b> (Sep 7 2026 – Jan 3 2027) are expected conditions, not observations: the crowd is the median of
      comparable past days (last year's same holiday, otherwise the same week last year, otherwise a typical weekday) and the
      service is TransLink's published timetable for that exact date.</div>
    <div>Times are Vancouver local time. The mobility data is a synthetic subscriber sample, so FlowGuard reports ratios to normal, never headcounts.
      Corridors use each device's home area as a proxy for the direction of outbound demand. Scheduled service comes from TransLink's Sept 2026 GTFS feed, applied by service day type (weekday / Saturday / Sunday-holiday), in bus-equivalents (SkyTrain and SeaBus ≈ 5 buses, West Coast Express ≈ 12).</div>`;
}

function syncUrl() {
  const u = new URL(location.href);
  u.searchParams.set("poi", state.poi);
  u.searchParams.set("date", state.date);
  u.searchParams.set("t", fmtSlot(state.slot));
  history.replaceState(null, "", u);
}

// ---------------------------------------------------------------- replay
function setSlot(i) {
  state.slot = Math.max(0, Math.min(47, i));
  renderAll();
  syncUrl();
}
function tick() {
  if (state.slot >= 47) return togglePlay(false);
  setSlot(state.slot + 1);
}
function togglePlay(on = !state.playing) {
  state.playing = on;
  $("#play").classList.toggle("playing", on);
  $("#play").setAttribute("aria-label", on ? "Pause" : "Play");
  clearInterval(state.timer);
  if (on) state.timer = setInterval(tick, 1500 / state.speed);
}

// ---------------------------------------------------------------- scenario lab
function syncLab() {
  const sc = state.sc;
  $("#crowd").value = sc.crowd; $("#crowdOut").textContent = `${sc.crowd > 0 ? "+" : ""}${sc.crowd}%`;
  $("#service").value = sc.service; $("#serviceOut").textContent = `${sc.service > 0 ? "+" : ""}${sc.service}%`;
  $("#eventSlot").value = sc.eventSlot; $("#eventSize").value = String(sc.eventSize);
}
async function preset(name) {
  if (name === "reset") state.sc = { crowd: 0, service: 0, eventSlot: 44, eventSize: 0 };
  if (name === "service") state.sc.service = -30;
  if (name === "event") { state.sc.eventSlot = 44; state.sc.eventSize = 1; state.slot = 41; }
  if (name === "surge") {
    const top = state.days.presets[0];
    state.sc = { crowd: 0, service: 0, eventSlot: 44, eventSize: 0 };
    state.slot = slotOf(state.day?.poi.replay_start || "10:00");
    syncLab();
    if (top && state.date !== top.date) return loadDay(top.date);
  }
  syncLab();
  renderAll();
  syncUrl();
}
function openDrawer(id, open = true) {
  for (const d of ["lab", "ask"]) $(`#${d}`).hidden = !(open && d === id);
  $("#labBtn").classList.toggle("active", open && id === "lab");
  document.body.classList.toggle("lab-open", open && id === "lab");
  if (radar) radar.resize();
}

/** ?selftest=1 — with no scenario, client readiness must equal the server's for every slot/group/horizon. */
function selfTest() {
  let n = 0, bad = 0;
  for (let t = 0; t < 48; t++) for (const g of state.day.group_order) {
    view(g, t).forEach((v, k) => { n++; if ((v.ready ?? null) !== (state.day.groups[g].rows[t].ready[k] ?? null)) bad++; });
  }
  const acts = [...Array(48).keys()].filter((t) => actionsAt(t).length).length;
  const srv = [...Array(48).keys()].filter((t) => state.day.group_order.some((g) => state.day.groups[g].rows[t].action)).length;
  document.body.dataset.selftest = `readiness_mismatch=${bad}/${n} action_slots_client=${acts} action_slots_server=${srv}`;
}

// ---------------------------------------------------------------- genie
async function ask(q) {
  const out = $("#askOut");
  out.insertAdjacentHTML("afterbegin", `<div class="ask-q">${esc(q)}</div><div class="caption" id="askWait">Thinking…</div>`);
  try {
    const r = await getJSON("/api/genie/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, conversation_id: state.genieConv }) });
    state.genieConv = r.conversation_id || state.genieConv;
    let html = r.error ? `<div class="caption">${esc(r.error)}</div>` : "";
    if (r.text) html += `<div>${esc(r.text)}</div>`;
    if (r.columns && r.rows) {
      html += `<table><tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${r.rows.slice(0, 20).map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</table>`;
    }
    $("#askWait").outerHTML = html || '<div class="caption">No answer.</div>';
  } catch (e) {
    $("#askWait").outerHTML = `<div class="caption">${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------- wiring
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  store.set("fg-theme", t);
  if (radar) { radarColors(radar); radar.update("none"); }
  if (state.day) renderAll();
}

function wire() {
  $("#day").addEventListener("change", (e) => loadDay(e.target.value));
  $("#play").addEventListener("click", () => togglePlay());
  $("#speed").addEventListener("click", () => {
    state.speed = { 1: 2, 2: 4, 4: 1 }[state.speed];
    $("#speed").textContent = `${state.speed}×`;
    if (state.playing) togglePlay(true);
  });
  $("#scrub").addEventListener("input", (e) => setSlot(Number(e.target.value)));
  $("#themeBtn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  $("#labBtn").addEventListener("click", () => openDrawer("lab", $("#lab").hidden));
  $("#askBtn").addEventListener("click", () => openDrawer("ask", $("#ask").hidden));
  document.addEventListener("click", (e) => {
    const sw = e.target.closest("[data-poi]");
    if (sw && sw.dataset.poi !== state.poi) loadPoi(sw.dataset.poi);
    const p = e.target.closest("[data-preset]");
    if (p) preset(p.dataset.preset);
    const c = e.target.closest("[data-close]");
    if (c) openDrawer(c.dataset.close, false);
  });
  $("#eventSlot").innerHTML = [...Array(48 - CHART_FROM).keys()].map((i) => `<option value="${i + CHART_FROM}">${fmtSlot(i + CHART_FROM)}</option>`).join("");
  for (const id of ["crowd", "service"]) {
    $(`#${id}`).addEventListener("input", (e) => { state.sc[id] = Number(e.target.value); syncLab(); renderAll(); });
  }
  $("#eventSlot").addEventListener("change", (e) => { state.sc.eventSlot = Number(e.target.value); renderAll(); });
  $("#eventSize").addEventListener("change", (e) => { state.sc.eventSize = Number(e.target.value); renderAll(); });
  $("#askForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#askInput").value.trim();
    if (q) { $("#askInput").value = ""; ask(q); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea")) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    if (e.key === "ArrowRight") setSlot(state.slot + 1);
    if (e.key === "ArrowLeft") setSlot(state.slot - 1);
    if (e.key === "Escape") openDrawer("lab", false);
  });
}

function renderPoiSwitch() {
  $("#poiSwitch").innerHTML = state.pois.map((p) =>
    `<button role="tab" data-poi="${p.key}" aria-selected="${p.key === state.poi}" title="${esc(p.full_name)}">${esc(p.name)}</button>`).join("");
  const p = state.pois.find((x) => x.key === state.poi);
  $("#tagline").textContent = `${p.full_name} · TransLink operations`;
  document.title = `FlowGuard · ${p.name}`;
}

/** Switch location: load its day list and model metrics, then a day (keep the replay time). */
async function loadPoi(key, wantDate) {
  togglePlay(false);
  state.poi = key;
  renderPoiSwitch();
  showLoader(`Loading ${poiName()}…`);
  try {
    [state.days, state.model] = await Promise.all([getJSON(`/api/days?poi=${key}`), getJSON(`/api/model?poi=${key}`)]);
  } catch (e) {
    hideLoader();
    return showError(`FlowGuard data is unavailable: ${e.message}`);
  }
  fillDays();
  renderTrust();
  const top = state.days.presets[0];
  $("#surgePreset").textContent = top ? `${top.label} replay` : "Surge day replay";
  const all = [...state.days.presets, ...state.days.days, ...(state.days.future || [])].map((d) => d.date);
  const date = all.includes(wantDate) ? wantDate : all.includes(state.days.default_date) ? state.days.default_date : all[0];
  await loadDay(date);
}

async function init() {
  const params = new URLSearchParams(location.search);
  applyTheme(params.get("theme") || store.get("fg-theme") || "dark");
  wire();
  syncLab();
  try {
    state.pois = await getJSON("/api/pois");
  } catch (e) {
    return showError(`FlowGuard data is unavailable: ${e.message}`);
  }
  const t = params.get("t");
  if (t && /^\d{2}:\d{2}$/.test(t)) state.slot = slotOf(t);
  if (params.get("lab") === "1") openDrawer("lab");
  const poi = state.pois.some((p) => p.key === params.get("poi")) ? params.get("poi") : state.pois[0].key;
  await loadPoi(poi, params.get("date"));
  if (params.get("selftest") === "1" && state.day) selfTest();
  getJSON("/api/genie/info").then((g) => { $("#askBtn").hidden = !g.configured; }).catch(() => {});
}

init();
