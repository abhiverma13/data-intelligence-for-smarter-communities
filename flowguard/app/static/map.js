/* FlowGuard map view: a Leaflet map (Esri basemap tiles, no API key) of each location's exit corridors with real
   TransLink route geometry (static/data/<poi>/map.json) and a per-route exit-wave timeline. It reads the same
   /api/day payload and the same client-side view()/actionsAt() maths as the radar, so map values always agree with
   the readiness table and the server. If the tiles can't load (offline), the committed land silhouette is drawn. */
"use strict";

window.FlowGuardMap = (function () {
  const READY_VAR = { Prepared: "--good", Watch: "--warning", Strained: "--serious", Critical: "--critical", "No service": "--none" };
  const SEVERITY = { Prepared: 0, Watch: 1, Strained: 2, Critical: 3 };
  const CORNER_ORDER = ["topleft", "bottomleft", "bottomright", "topright"];   // fallback if map.json has no corner
  const BAND_W = 252, BAND_H = 88;
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const TILES = {                        // Esri basemaps: no API key; attribution required
    light: [ESRI + "World_Street_Map/MapServer/tile/{z}/{y}/{x}"],
    dark: [ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
  };
  const ATTRIB = 'Basemap &copy; <a href="https://www.esri.com">Esri</a>, HERE, Garmin, &copy; OpenStreetMap contributors · Routes: TransLink GTFS';

  const geos = {}, pending = {};                  // map.json per POI key
  let host = null, geo = null, poiKey = null, failed = null;
  let map = null, tiles = null, tileTheme = null, tileOk = false, tileErr = 0, landLayer = null, kpiEl = null;
  let layers = null;                               // per-POI: { routes: {g: {...}}, wp, bands: {g: el}, controls: [] }

  const col = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const readyColor = (lvl) => col(READY_VAR[lvl] || "--none");
  const ll = (line) => line.map((p) => [p[1], p[0]]);     // map.json is [lon, lat]; Leaflet wants [lat, lon]
  const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
  const worstOf = (ready) => ready.includes("Critical") ? "Critical" : ready.includes("Strained") ? "Strained"
    : ready.includes("Watch") ? "Watch" : "Prepared";

  function loadGeo(key, cb) {
    if (geos[key]) return cb();
    if (pending[key]) { pending[key].cb = cb; return; }
    pending[key] = { cb };
    fetch(`/static/data/${encodeURIComponent(key)}/map.json`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((g) => { geos[key] = g; const p = pending[key]; delete pending[key]; p.cb(); })
      .catch((e) => {
        delete pending[key]; failed = key;
        if (host && !map) host.innerHTML = `<div class="map-msg">Map geometry unavailable (${esc(e.message)}).</div>`;
        else console.warn(`FlowGuard map: no geometry for ${key} (${e.message})`);
      });
  }

  // ------------------------------------------------------------ per-route exit-wave band (now → +120)
  function bandSvg(g, fg) {
    const day = fg.state.day, t = fg.state.slot, G = day.groups[g], rows = G.rows;
    if (!rows || !rows[t]) return "";
    const v = fg.view(g, t);                                   // scenario-aware; mirrors the readiness table
    const ready = v.map((r) => r.ready), dep = v.map((r) => r.dem);
    const worst = worstOf(ready);
    const nowVal = t >= 1 && rows[t - 1] && rows[t - 1].act ? rows[t - 1].act[0] : null;
    const normal = G.typical_demand || 0;

    const vals = [nowVal, normal, ...dep].filter((z) => z != null);
    const ymax = Math.max(1, ...vals) * 1.18;
    const x0 = 34, x1 = BAND_W - 12, yTop = 28, yBot = BAND_H - 18;
    const X = (i) => x0 + (i / 4) * (x1 - x0);                 // 0=now, 1..4 = +30..+120
    const Y = (val) => yBot - (Math.max(0, val) / ymax) * (yBot - yTop);

    const fc = [1, 2, 3, 4].map((i) => [i, dep[i - 1], ready[i - 1]]).filter((p) => p[1] != null);
    const fcPath = fc.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    const nowPt = nowVal == null ? "" : `<circle cx="${X(0).toFixed(1)}" cy="${Y(nowVal).toFixed(1)}" r="6" fill="${col("--series-actual")}" stroke="${col("--surface")}" stroke-width="2"><title>${esc(`${G.label} · now (${fg.fmtSlot(t)})\nActual exits so far this slot (sample scale)`)}</title></circle>`;

    const dots = fc.map((p) => {
      const i = p[0], m = v[i - 1];
      const tip = `${G.label} · +${i * 30} min (${fg.fmtSlot(t + i)})\nExit demand ${m.idx == null ? "–" : m.idx.toFixed(1) + "× normal"}\nScheduled trips ${m.svc == null ? "–" : m.svc.toFixed(1)} / 30 min\nGap ${m.gap == null ? "–" : m.gap.toFixed(1) + "× per trip"} · ${p[2]}`;
      return `<circle cx="${X(i).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="5.5" fill="${readyColor(p[2])}" stroke="${col("--surface")}" stroke-width="2"><title>${esc(tip)}</title></circle>`;
    }).join("");

    const labels = ["now", "+30", "+60", "+90", "+120"].map((s, i) =>
      `<text x="${X(i).toFixed(1)}" y="${BAND_H - 5}" text-anchor="middle" font-size="12" fill="${col("--muted")}">${s}</text>`).join("");

    return `<svg class="map-band" width="${BAND_W}" height="${BAND_H}" viewBox="0 0 ${BAND_W} ${BAND_H}">
      <text x="12" y="18" font-size="15" font-weight="700" fill="${col("--ink")}">${esc(G.label)}</text>
      <text x="${BAND_W - 12}" y="18" text-anchor="end" font-size="13" fill="${readyColor(worst)}">${esc(worst)}</text>
      <line x1="${x0}" x2="${x1}" y1="${Y(normal).toFixed(1)}" y2="${Y(normal).toFixed(1)}" stroke="${col("--series-usual")}" stroke-width="1.3" stroke-dasharray="3 3"/>
      <text x="6" y="${(Y(normal) + 4).toFixed(1)}" font-size="9" fill="${col("--muted")}">normal</text>
      <line x1="${x0}" x2="${x1}" y1="${yBot}" y2="${yBot}" stroke="${col("--axis")}" stroke-width="1"/>
      ${fcPath ? `<path d="${fcPath}" fill="none" stroke="${col("--series-forecast")}" stroke-width="2.2" stroke-dasharray="5 3" stroke-linejoin="round"/>` : ""}
      ${nowPt}${dots}${labels}
    </svg>`;
  }

  // ------------------------------------------------------------ map setup (once) and per-POI layers
  function panel(corner, cls) {
    const Ctl = L.Control.extend({ onAdd() { const d = L.DomUtil.create("div", `map-panel ${cls}`); L.DomEvent.disableClickPropagation(d); L.DomEvent.disableScrollPropagation(d); return d; } });
    const c = new Ctl({ position: corner });
    c.addTo(map);
    return c;
  }

  function setTiles() {
    const th = theme();
    if (th === tileTheme) return;
    if (tiles) map.removeLayer(tiles);
    tileTheme = th;
    tiles = L.layerGroup(TILES[th].map((url, i) => {
      const l = L.tileLayer(url, { maxZoom: th === "dark" ? 16 : 19, opacity: i ? 0.55 : 1, attribution: i ? "" : ATTRIB });  // dim reference labels under ours
      l.on("tileload", () => { tileOk = true; if (landLayer) { map.removeLayer(landLayer); landLayer = null; host.classList.remove("map-offline"); } });
      l.on("tileerror", () => { if (!tileOk && ++tileErr >= 4) showLand(); });
      return l;
    })).addTo(map);
  }

  /** Offline fallback: the committed Natural Earth silhouette over the map-water background. */
  function showLand() {
    host.classList.add("map-offline");
    if (landLayer) map.removeLayer(landLayer);
    landLayer = L.layerGroup(geo.land.map((ring) => L.polygon(ll(ring),
      { color: col("--map-land-edge"), weight: 1, fillColor: col("--map-land"), fillOpacity: 1, interactive: false }))).addTo(map);
    landLayer.eachLayer((l) => l.bringToBack());
  }

  function fit() {                                   // vertical padding keeps lines clear of the corner bands
    const b = geo.bbox;
    map.fitBounds([[b[1], b[0]], [b[3], b[2]]], { paddingTopLeft: [12, 92], paddingBottomRight: [12, 92] });
  }

  function create() {
    host.innerHTML = "";
    map = L.map(host, { zoomControl: false, scrollWheelZoom: false, zoomSnap: 0.25, attributionControl: true });
    map.attributionControl.setPrefix(false);
    map.on("click focus", () => map.scrollWheelZoom.enable());   // don't hijack page scroll until the map is used
    map.on("mouseout", () => map.scrollWheelZoom.disable());
    map.createPane("routes").style.zIndex = 420;

    kpiEl = panel("topright", "map-kpi").getContainer();
    L.control.zoom({ position: "topright" }).addTo(map);
    const Reset = L.Control.extend({ onAdd() {
      const b = L.DomUtil.create("button", "map-reset btn small");
      b.type = "button"; b.title = "Reset view"; b.textContent = "Fit";
      L.DomEvent.on(b, "click", (e) => { L.DomEvent.stop(e); fit(); });
      return b;
    } });
    new Reset({ position: "topright" }).addTo(map);
    setTiles();
    new ResizeObserver(() => { if (host.offsetWidth) map.invalidateSize(); }).observe(host);
  }

  /** Hovering a band card highlights its route group's lines and fades the others. */
  function focusGroup(g) {
    Object.entries(layers.routes).forEach(([k, R]) => {
      const on = g == null || k === g;
      R.line.setStyle({ opacity: on ? 0.92 : 0.25, weight: R.line._fgW + (g != null && k === g ? 3 : 0) });
      R.casing.setStyle({ opacity: on ? 0.95 : 0.2 });
    });
  }

  function dropLayers() {
    if (!layers) return;
    Object.values(layers.routes).forEach((R) => R.group.remove());
    layers.wp.remove();
    layers.controls.forEach((c) => c.remove());
    if (landLayer) { map.removeLayer(landLayer); landLayer = null; }
    layers = null;
  }

  function buildLayers(fg) {
    dropLayers();
    layers = { routes: {}, wp: L.layerGroup().addTo(map), bands: {}, controls: [] };
    const order = geo.group_order || fg.state.day.group_order;
    order.forEach((g, i) => {
      const G = geo.groups[g];
      if (!G) return;
      const lines = G.lines.map(ll);
      const casing = L.polyline(lines, { pane: "routes", color: col("--surface"), opacity: 0.95, lineCap: "round", lineJoin: "round", interactive: false });
      const line = L.polyline(lines, { pane: "routes", opacity: 0.92, lineCap: "round", lineJoin: "round" });
      line._fgW = 6;
      line.bindTooltip("", { sticky: true, className: "map-tip" });
      line.on("mouseover", () => focusGroup(g));
      line.on("mouseout", () => focusGroup(null));
      layers.routes[g] = { casing, line, group: L.layerGroup([casing, line]).addTo(map) };
      const ctl = panel(G.corner || CORNER_ORDER[i % CORNER_ORDER.length], "map-bandbox");
      const el = ctl.getContainer();
      el.addEventListener("mouseenter", () => focusGroup(g));
      el.addEventListener("mouseleave", () => focusGroup(null));
      layers.controls.push(ctl);
      layers.bands[g] = el;
    });
    geo.waypoints.forEach((p) => {
      const hub = p.kind === "hub";
      const m = L.circleMarker([p.lat, p.lon], { pane: "markerPane", radius: hub ? 9 : 4, weight: hub ? 3 : 1.5, fillOpacity: 1, className: hub ? "map-hub" : "map-wp" });
      m.bindTooltip(esc(p.label), { permanent: true, direction: "right", offset: [hub ? 10 : 6, 0], className: hub ? "map-label hub" : "map-label" });
      m._fgHub = hub;
      m.addTo(layers.wp);
    });
    if (host.classList.contains("map-offline")) showLand();
    fit();
  }

  // ------------------------------------------------------------ per-tick restyle
  function update(fg) {
    const day = fg.state.day, t = fg.state.slot;
    setTiles();
    if (landLayer) landLayer.eachLayer((l) => l.setStyle({ color: col("--map-land-edge"), fillColor: col("--map-land") }));
    layers.wp.eachLayer((m) => m.setStyle({ color: col("--surface"), fillColor: m._fgHub ? col("--accent") : col("--ink-2") }));

    const sev = [];
    day.group_order.forEach((g) => {
      const R = layers.routes[g], G = day.groups[g], rows = G && G.rows;
      if (!R) return;
      if (!rows || !rows[t]) { R.group.remove(); return; }
      R.group.addTo(map);
      const v = fg.view(g, t), worst = worstOf(v.map((r) => r.ready));
      const idx = v[0] && v[0].idx;
      const w = Math.min(11, 4 + (idx || 1) * 2);
      R.line._fgW = w;
      R.casing.setStyle({ weight: w + 4, color: col("--surface") });
      R.line.setStyle({ weight: w, color: readyColor(worst) });
      R.line.setTooltipContent(`<b>${esc(G.label)}</b> · ${esc(worst)}<br>${esc(G.routes || "")}<br>Next 30 min: ${idx == null ? "–" : idx.toFixed(1) + "× normal exit demand"}`);
      sev.push([SEVERITY[worst] || 0, R]);
      if (layers.bands[g]) layers.bands[g].innerHTML = bandSvg(g, fg);
    });
    sev.sort((a, b) => a[0] - b[0]).forEach(([, R]) => { R.casing.bringToFront(); R.line.bringToFront(); });  // worst on top

    const p = day.slots[t];
    kpiEl.innerHTML = `<div class="caption">${esc(day.poi.name)} now</div><div><b>${p && p.pressure != null ? p.pressure.toFixed(1) + "×" : "–"}</b> × normal occupancy</div>`;
  }

  function render(fg) {
    host = host || document.getElementById("mapbox");
    if (!host || !fg.state.day) return;
    const key = fg.state.day.poi.key;                         // the loaded day's POI, so layers and data always match
    if (!window.L) { host.innerHTML = '<div class="map-msg">Map library unavailable.</div>'; return; }
    if (!geos[key]) {
      if (failed === key) return;
      if (!map) host.innerHTML = '<div class="map-msg">Loading map…</div>';
      loadGeo(key, () => render(fg));
      return;
    }
    if (!map) create();
    else if (host.offsetWidth) map.invalidateSize();
    if (key !== poiKey) { geo = geos[key]; poiKey = key; buildLayers(fg); }
    update(fg);
  }

  function init() { host = document.getElementById("mapbox"); }

  return { init, render };
})();
