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
const LEVERS = {
  EASTBOUND: (s) => [`Stage supplemental eastbound (R2-direction) trips at Park Royal exchange from ${s}`,
    "Position passenger-management staff at the eastbound bays",
    "Message riders: consider a later departure or alternative routes"],
  DOWNTOWN: (s) => [`Add short-turn downtown trips (250/257 direction) from ${s}`,
    "Flag southbound Lions Gate pressure to bridge operations",
    "Staff the downtown bays"],
  WEST_VAN_LOCAL: (s) => [`Ask West Vancouver Blue Bus to hold a spare local bus at Park Royal from ${s}`,
    "Open overflow wayfinding at the local bays"],
};

const state = {
  days: null, model: null, day: null, date: null, slot: 26, playing: false, speed: 1, timer: null, source: "", view: "map",
  sc: { crowd: 0, service: 0, eventSlot: 44, eventSize: 0 },
};
let radar = null;

// ---------------------------------------------------------------- helpers
const fmtSlot = (i) => {
  const m = i * SLOT_MIN;
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
};
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
      gap = dem / Math.max(svc, 0.5) / G.typical_load;
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
  $("#pressureCaption").textContent = `People on site vs a normal ${DAY_NAMES[d.day_type]} at ${s.t}`;

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
  const ns = (s.shares.NS_WEST ?? 0) + (s.shares.NS_EAST ?? 0);
  const nsB = (s.base_shares.NS_WEST ?? 0) + (s.base_shares.NS_EAST ?? 0);
  $("#offNS").innerHTML = `<b>${pct(1 - ns)}</b> of arrivals must leave the North Shore <span>(normal ${pct(1 - nsB)})</span>`;
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
  const tag = scenarioActive() ? " <small>(scenario)</small>" : "";
  if (!acts.length) {
    card.style.setProperty("--st", css("--good"));
    card.innerHTML = `<div class="action-top">${chip("Prepared")}
      <div class="action-title">All route groups prepared for the next 2 hours${tag}</div></div>
      <p class="action-why">Expected exit demand per scheduled trip is within the normal range for the ${esc(d.service_label)} schedule. No action needed.</p>`;
    return;
  }
  const a = acts[0], G = d.groups[a.g];
  card.style.setProperty("--st", css(a.worst === "Critical" ? "--critical" : "--serious"));
  const also = acts.slice(1).map((x) => `<div class="also-row">${chip(x.worst)}
    <span><b>${esc(d.groups[x.g].label)}</b> ${fmtSlot(x.start)}–${fmtSlot(x.end)} · in ${x.lead} min · ${x.peak.toFixed(1)}× normal per trip</span></div>`).join("");
  card.innerHTML = `
    <div class="action-top">${chip(a.worst)}
      <div class="action-title">${esc(G.label)} ${a.worst.toLowerCase()} ${fmtSlot(a.start)}–${fmtSlot(a.end)}${tag}</div>
      <div class="lead"><b>in ${a.lead} min</b><span>lead time</span></div></div>
    <p class="action-why">Expected ${esc(G.label.toLowerCase())} exit demand per scheduled trip is <b>${a.peak.toFixed(1)}×</b> a normal ${esc(d.service_label)}, on the ${esc(d.service_label)} schedule (routes ${esc(G.routes)}).</p>
    <ul class="levers">${LEVERS[a.g](a.stage).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
    ${also ? `<div class="also">${also}</div>` : ""}`;
}

function radarData() {
  const d = state.day, t = state.slot, e = d.exit, f = 100 / e.normal_peak, sc = state.sc;
  const idx = [...Array(48 - CHART_FROM).keys()].map((i) => i + CHART_FROM);
  const actual = idx.map((i) => (i <= t ? e.actual[i] * f : null));
  const usual = idx.map((i) => (e.usual[i] == null ? null : e.usual[i] * f));
  const forecast = idx.map((i) => {
    if (i === t) return e.actual[t] * f;
    const k = i - t;
    if (k < 1 || k > 4) return null;
    const v = e.forecast[t]?.[k - 1];
    return v == null ? null : (v * (1 + sc.crowd / 100) + extraTotal(i)) * f;
  });
  const earlier = idx.map((i) => (i <= t && i >= 2 && e.forecast[i - 2]?.[1] != null ? e.forecast[i - 2][1] * f : null));
  return { labels: idx.map(fmtSlot), actual, usual, forecast, earlier, nowIdx: t - CHART_FROM };
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
  const [a, f, e, u] = chart.data.datasets;
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
  ds[0].data = r.actual; ds[1].data = r.forecast; ds[2].data = r.earlier; ds[3].data = r.usual;
  radar.$now = r.nowIdx;
  radar.update();
}

function renderReady() {
  const d = state.day, t = state.slot;
  const head = `<thead><tr><th>Route group</th><th>Exit demand<br>+60 min</th><th>Scheduled<br>trips / 30 min</th>
    ${HORIZONS.map((h) => `<th>+${h * SLOT_MIN} min<br><span style="text-transform:none">${fmtSlot(t + h)}</span></th>`).join("")}</tr></thead>`;
  const rows = d.group_order.map((g) => {
    const G = d.groups[g], v = view(g, t), mid = v[1];
    return `<tr><td class="grp"><b>${esc(G.label)}</b><small>${esc(G.routes)}</small></td>
      <td class="num">${x2(mid.idx)} <small>normal</small></td>
      <td class="num">${mid.svc == null ? "–" : mid.svc.toFixed(1)}</td>
      ${v.map((x) => `<td><div class="cell">${chip(x.ready)}<span class="gap">${x.gap == null ? "&nbsp;" : `${x.gap.toFixed(1)}× per trip`}</span></div></td>`).join("")}</tr>`;
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
  renderAction(); renderHero(); renderReady(); renderBanner();
}

/** The hero slot is either the radar chart or the map; both read the same scenario-aware maths. */
function renderHero() {
  if (!state.day) return;
  if (state.view === "map") { if (window.FlowGuardMap) window.FlowGuardMap.render(window.FG); }
  else renderRadar();
}

function applyView(v) {
  state.view = v === "radar" ? "radar" : "map";
  const isMap = state.view === "map";
  $("#chartbox").hidden = isMap;
  $("#mapbox").hidden = !isMap;
  $("#radarLegend").hidden = isMap;
  $("#mapLegend").hidden = !isMap;
  document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
  $("#heroSub").textContent = isMap
    ? "corridors on real TransLink routes · exit index × normal · dot colour = readiness"
    : "exit wave · index: normal-day peak = 100";
  if (!isMap && radar) radar.resize();
  renderHero();
}

// ---------------------------------------------------------------- data loading
function showError(msg) {
  const b = $("#banner");
  b.hidden = false; b.className = "banner error"; b.textContent = msg;
}

async function loadDay(date) {
  $("#main").classList.add("loading");
  try {
    const day = await getJSON(`/api/day?date=${date}`);
    state.day = day; state.date = date; state.source = day.source;
    $("#day").value = date;
    $("#banner").classList.remove("error");
    $("#source").textContent = day.source === "live" ? "Data: live · Databricks SQL (gold tables)" : "Data: offline snapshot of the gold tables";
    syncUrl();
    renderAll();
  } catch (e) {
    showError(`Couldn't load ${fmtDate(date)}: ${e.message}`);
  } finally {
    $("#main").classList.remove("loading");
  }
}

function fillDays() {
  const opt = (d, prefix = "") => `<option value="${d.date}">${prefix}${fmtDate(d.date)}${d.label ? ` · ${esc(d.label)}` : ""} · ${d.surge_ratio.toFixed(2)}× usual</option>`;
  const preset = new Set(state.days.presets.map((d) => d.date));
  $("#day").innerHTML = `<optgroup label="Demo days">${state.days.presets.map((d) => opt(d)).join("")}</optgroup>
    <optgroup label="All days (daily arrivals vs usual)">${state.days.days.filter((d) => !preset.has(d.date)).map((d) => opt(d)).join("")}</optgroup>`;
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
    <div>Times are Vancouver local time. The mobility data is a synthetic subscriber sample, so FlowGuard reports ratios to normal, never headcounts.
      Corridors use each device's home area as a proxy for the direction of outbound demand. Scheduled trips come from TransLink's Sept 2026 GTFS feed, applied by service day type (weekday / Saturday / Sunday-holiday).</div>`;
}

function syncUrl() {
  const u = new URL(location.href);
  u.searchParams.set("date", state.date);
  u.searchParams.set("t", fmtSlot(state.slot));
  u.searchParams.set("view", state.view);
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
  if (name === "boxing") {
    state.sc = { crowd: 0, service: 0, eventSlot: 44, eventSize: 0 };
    state.slot = 20;
    syncLab();
    if (state.date !== "2025-12-26") return loadDay("2025-12-26");
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
  const mapMismatch = state.day.group_order.reduce((n, g) =>
    n + view(g, state.slot).filter((x, k) => (x.ready ?? null) !== (state.day.groups[g].rows[state.slot].ready[k] ?? null)).length, 0);
  document.body.dataset.selftest = `readiness_mismatch=${bad}/${n} action_slots_client=${acts} action_slots_server=${srv} map_mismatch=${mapMismatch}`;
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
    state.speed = state.speed === 1 ? 4 : 1;
    $("#speed").textContent = `${state.speed}×`;
    if (state.playing) togglePlay(true);
  });
  $("#scrub").addEventListener("input", (e) => setSlot(Number(e.target.value)));
  $("#themeBtn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => { applyView(b.dataset.view); syncUrl(); }));
  $("#labBtn").addEventListener("click", () => openDrawer("lab", $("#lab").hidden));
  $("#askBtn").addEventListener("click", () => openDrawer("ask", $("#ask").hidden));
  document.addEventListener("click", (e) => {
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

async function init() {
  const params = new URLSearchParams(location.search);
  applyTheme(params.get("theme") || store.get("fg-theme") || "dark");
  applyView(params.get("view") || "map");
  wire();
  syncLab();
  try {
    [state.days, state.model] = await Promise.all([getJSON("/api/days"), getJSON("/api/model")]);
  } catch (e) {
    return showError(`FlowGuard data is unavailable: ${e.message}`);
  }
  fillDays();
  renderTrust();
  const t = params.get("t");
  if (t && /^\d{2}:\d{2}$/.test(t)) state.slot = Number(t.slice(0, 2)) * 2 + Math.floor(Number(t.slice(3)) / 30);
  const all = [...state.days.presets, ...state.days.days].map((d) => d.date);
  const want = params.get("date");
  const date = all.includes(want) ? want : all.includes("2026-04-25") ? "2026-04-25" : all[0];
  if (params.get("lab") === "1") openDrawer("lab");
  await loadDay(date);
  if (params.get("selftest") === "1" && state.day) selfTest();
  getJSON("/api/genie/info").then((g) => { $("#askBtn").hidden = !g.configured; }).catch(() => {});
}

window.FG = { state, view, actionsAt, HORIZONS, SLOT_MIN, fmtSlot, fmtMin, pct, x2, esc, css, chip, STATUS, ICONS, SEVERE, renderAll };

init();
