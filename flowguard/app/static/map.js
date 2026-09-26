/* FlowGuard map view: a Leaflet map (Esri basemap tiles, no API key) of each location's exit corridors with real
   TransLink route geometry (static/data/<poi>/map.json) and a per-route exit-wave strip under the map. It reads the
   same /api/day payload and the same client-side view()/actionsAt() maths as the radar, so map values always agree
   with the readiness table and the server. If the tiles can't load (offline), the committed land silhouette is drawn.

   Encoding: line colour = worst readiness over the next 2 h, line width = next-30-min exit demand, moving dots =
   the direction the crowd leaves (faster = more demand), hub ring = on-site pressure. */
"use strict";

window.FlowGuardMap = (function () {
  const READY_VAR = { Prepared: "--good", Watch: "--warning", Strained: "--serious", Critical: "--critical", "No service": "--none" };
  const LEVEL_VAR = { Normal: "--good", Elevated: "--warning", High: "--serious", Severe: "--critical" };
  const SEVERITY = { Prepared: 0, Watch: 1, Strained: 2, Critical: 3 };
  const BAND_W = 280, BAND_H = 86;
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const TILES = {                        // Esri canvas basemaps: no API key; attribution required; quiet so routes lead
    light: [ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", ESRI + "Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
    dark: [ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
  };
  const ATTRIB = 'Basemap &copy; <a href="https://www.esri.com">Esri</a>, HERE, Garmin, &copy; OpenStreetMap contributors · Routes: TransLink GTFS';

  const geos = {}, pending = {};                  // map.json per POI key
  let host = null, strip = null, geo = null, poiKey = null, failed = null;
  let map = null, tiles = null, tileTheme = null, tileOk = false, tileErr = 0, landLayer = null, kpiEl = null;
  let layers = null;         // per-POI: { routes: {g: {...}}, wp, hub: {ring, dot} }
  let focused = null;        // group zoomed to by a click, or null for the whole corridor view
  let needFit = false;       // fit once the end labels have content, so their widths set the side padding

  const col = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const readyColor = (lvl) => col(READY_VAR[lvl] || "--none");
  const ll = (line) => line.map((p) => [p[1], p[0]]);     // map.json is [lon, lat]; Leaflet wants [lat, lon]
  const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
  const worstOf = (ready) => ready.includes("Critical") ? "Critical" : ready.includes("Strained") ? "Strained"
    : ready.includes("Watch") ? "Watch" : ready.includes("Prepared") ? "Prepared" : "No service";

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

  // ------------------------------------------------------------ per-route exit-wave chart (now → +120)
  function bandSvg(g, fg, v) {
    const day = fg.state.day, t = fg.state.slot, G = day.groups[g], rows = G.rows;
    const ready = v.map((r) => r.ready), dep = v.map((r) => r.dem);
    const nowVal = t >= 1 && rows[t - 1] && rows[t - 1].act ? rows[t - 1].act[0] : null;
    const normal = G.typical_demand || 0;

    const vals = [nowVal, normal, ...dep].filter((z) => z != null);
    const ymax = Math.max(1, ...vals) * 1.15;
    const x0 = 50, x1 = BAND_W - 20, yTop = 8, yBot = BAND_H - 22;
    const X = (i) => x0 + (i / 4) * (x1 - x0);                 // 0=now, 1..4 = +30..+120
    const Y = (val) => yBot - (Math.max(0, val) / ymax) * (yBot - yTop);

    const fc = [1, 2, 3, 4].map((i) => [i, dep[i - 1], ready[i - 1]]).filter((p) => p[1] != null);
    const fcPath = fc.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    const nowPt = nowVal == null ? "" : `<circle cx="${X(0).toFixed(1)}" cy="${Y(nowVal).toFixed(1)}" r="5.5" fill="${col("--series-actual")}" stroke="${col("--surface-2")}" stroke-width="2"><title>${esc(`${G.label} · now (${fg.fmtSlot(t)})\nActual exits so far this slot (sample scale)`)}</title></circle>`;

    const dots = fc.map((p) => {
      const i = p[0], m = v[i - 1];
      const tip = `${G.label} · +${i * 30} min (${fg.fmtSlot(t + i)})\nExit demand ${m.idx == null ? "–" : m.idx.toFixed(1) + "× normal"}\nScheduled trips ${m.svc == null ? "–" : m.svc.toFixed(1)} / 30 min\nGap ${m.gap == null ? "–" : m.gap.toFixed(1) + "× per trip"} · ${p[2]}`;
      return `<circle cx="${X(i).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="5.5" fill="${readyColor(p[2])}" stroke="${col("--surface-2")}" stroke-width="2"><title>${esc(tip)}</title></circle>`;
    }).join("");

    const labels = ["now", "+30", "+60", "+90", "+120"].map((s, i) =>
      `<text x="${X(i).toFixed(1)}" y="${BAND_H - 5}" text-anchor="middle" font-size="12" fill="${col("--muted")}">${s}</text>`).join("");
    const yn = Y(normal);

    return `<svg class="band-chart" viewBox="0 0 ${BAND_W} ${BAND_H}" role="img" aria-label="${esc(G.label)} exit demand, next two hours">
      <line x1="${x0}" x2="${x1}" y1="${yn.toFixed(1)}" y2="${yn.toFixed(1)}" stroke="${col("--series-usual")}" stroke-width="1.3" stroke-dasharray="3 3"/>
      <text x="${x0 - 8}" y="${(yn + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="${col("--muted")}">normal</text>
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
      const l = L.tileLayer(url, { maxZoom: 16, opacity: i ? 0.6 : 1, attribution: i ? "" : ATTRIB });  // dim reference labels under ours
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

  /** Whole-corridor view, or one group's lines when a route is clicked. Starts with tight padding, then widens each side
      by however far an end label would spill past the edge (or under the top-right controls) at the target zoom. */
  function fit(g = null) {
    focused = g;
    const one = g && layers && layers.routes[g];
    const bb = geo.bbox;
    const bounds = one ? one.line.getBounds().extend([geo.hub.lat, geo.hub.lon]) : L.latLngBounds([[bb[1], bb[0]], [bb[3], bb[2]]]);
    const size = map.getSize(), ctl = kpiEl.parentElement;          // Leaflet's top-right control stack
    const ctlW = ctl.offsetWidth + 16, ctlH = ctl.offsetHeight + 16;
    const pad = { l: 24, r: 24, t: 64, b: 28 };
    const opts = () => ({ paddingTopLeft: [pad.l, pad.t], paddingBottomRight: [pad.r, pad.b] });
    for (let k = 0; k < 4 && layers; k++) {
      const v = map._getBoundsCenterZoom(bounds, opts()), c = map.project(v.center, v.zoom);
      let oL = 0, oR = 0;
      Object.values(layers.routes).forEach((R) => {
        const el = R.end.getTooltip().getElement();
        if ((one && R !== one) || !el || !el.offsetWidth) return;
        const p = map.project(R.end.getLatLng(), v.zoom).subtract(c).add(size.divideBy(2));
        const edge = p.y < ctlH ? size.x - ctlW : size.x - 12;
        if (R.endDir === "right") oR = Math.max(oR, p.x + 10 + el.offsetWidth - edge);
        else oL = Math.max(oL, 12 + el.offsetWidth + 10 - p.x);
      });
      if (oL <= 1 && oR <= 1) break;
      pad.l = Math.min(size.x * 0.4, pad.l + Math.max(0, oL) + 4);
      pad.r = Math.min(size.x * 0.4, pad.r + Math.max(0, oR) + 4);
    }
    if (one) map.flyToBounds(bounds, { ...opts(), duration: 0.6 });
    else map.fitBounds(bounds, opts());
    if (layers) Object.entries(layers.routes).forEach(([k, R]) => R.card && R.card.classList.toggle("selected", k === g));
  }

  function legendHtml(fg) {
    const row = (lvl) => { const [cls, icon] = fg.STATUS[lvl]; return `<li><span class="lg-dot ${cls}">${fg.ICONS[icon]}</span>${lvl}</li>`; };
    return `<span class="lg-title">Line colour = worst readiness, next 2 h</span><ul>${["Prepared", "Watch", "Strained", "Critical"].map(row).join("")}</ul>
      <span class="lg-key"><span class="lg-width"></span>Width = exit demand</span>
      <span class="lg-key"><span class="lg-flow"></span>Moving dots = where the crowd heads</span>
      <span class="lg-key"><span class="lg-ring"></span>Ring = on-site crowd</span>`;
  }

  function create(fg) {
    host.innerHTML = "";
    map = L.map(host, { zoomControl: false, scrollWheelZoom: false, zoomSnap: 0.25, attributionControl: true,
      fadeAnimation: false });   // no tile fade: it relies on rAF, which the flow animation can starve in headless captures
    map.attributionControl.setPrefix(false);
    map.on("click focus", () => map.scrollWheelZoom.enable());   // don't hijack page scroll until the map is used
    map.on("mouseout", () => map.scrollWheelZoom.disable());
    map.createPane("routes").style.zIndex = 420;
    const flowPane = map.createPane("flow");               // animated dots and end markers above the routes
    flowPane.style.zIndex = 430;

    kpiEl = panel("topright", "map-kpi").getContainer();
    L.control.zoom({ position: "topright" }).addTo(map);
    const Reset = L.Control.extend({ onAdd() {
      const b = L.DomUtil.create("button", "map-reset btn small");
      b.type = "button"; b.title = "Show all routes"; b.textContent = "Fit";
      L.DomEvent.on(b, "click", (e) => { L.DomEvent.stop(e); fit(); });
      return b;
    } });
    new Reset({ position: "topright" }).addTo(map);
    document.getElementById("mapKey").innerHTML = legendHtml(fg);
    setTiles();
    let lastW = host.offsetWidth;
    new ResizeObserver(() => {
      if (!host.offsetWidth) return;
      map.invalidateSize();
      if (layers && Math.abs(host.offsetWidth - lastW) > 40) fit(focused);
      lastW = host.offsetWidth;
    }).observe(host);
  }

  /** Hovering a route, its end label or its card highlights that group and fades the others. */
  function focusGroup(g) {
    Object.entries(layers.routes).forEach(([k, R]) => {
      const on = g == null || k === g, hi = g != null && k === g;
      R.line.setStyle({ opacity: on ? 0.95 : 0.2, weight: R.line._fgW + (hi ? 3 : 0) });
      R.casing.setStyle({ opacity: on ? 0.95 : 0.15, weight: R.line._fgW + 4 + (hi ? 3 : 0) });
      R.flow.setStyle({ opacity: on ? 0.9 : 0 });
      const lab = R.end.getTooltip().getElement();
      if (lab) lab.classList.toggle("faded", !on);
      if (R.card) R.card.classList.toggle("hover", hi);
    });
  }

  function dropLayers() {
    if (!layers) return;
    Object.values(layers.routes).forEach((R) => R.group.remove());
    layers.wp.remove();
    layers.hub.remove();
    if (landLayer) { map.removeLayer(landLayer); landLayer = null; }
    strip.innerHTML = "";
    layers = null;
  }

  function buildLayers(fg) {
    dropLayers();
    layers = { routes: {}, wp: L.layerGroup().addTo(map), hub: L.layerGroup() };
    const b = geo.bbox;
    map.fitBounds([[b[1], b[0]], [b[3], b[2]]]);             // a view first: Leaflet opens permanent tooltips only once loaded
    const hubLL = [geo.hub.lat, geo.hub.lon];
    const order = geo.group_order || fg.state.day.group_order;
    order.forEach((g) => {
      const G = geo.groups[g];
      if (!G) return;
      const lines = G.lines.map(ll);                            // every line starts at the hub, so dashes flow outward
      const casing = L.polyline(lines, { pane: "routes", color: col("--surface"), opacity: 0.95, lineCap: "round", lineJoin: "round", interactive: false });
      const line = L.polyline(lines, { pane: "routes", opacity: 0.95, lineCap: "round", lineJoin: "round" });
      const flow = L.polyline(lines, { pane: "flow", className: "map-flow", color: "#fff", opacity: 0.9, dashArray: "0.1 16", lineCap: "round", interactive: false });
      line._fgW = 6;
      line.bindTooltip("", { sticky: true, className: "map-tip" });
      line.on("mouseover", () => focusGroup(g));
      line.on("mouseout", () => focusGroup(null));
      line.on("click", () => fit(focused === g ? null : g));

      // Corridor end: a terminus dot with a permanent label (name + readiness chip) facing away from the hub
      const endLL = [G.end[1], G.end[0]], east = G.end[0] >= geo.hub.lon;
      const end = L.circleMarker(endLL, { pane: "flow", radius: 5, weight: 2.5, fillOpacity: 1, interactive: false });
      end.bindTooltip("", { permanent: true, direction: east ? "right" : "left", offset: [east ? 8 : -8, 0], className: "map-endlabel", interactive: true });

      const R = { casing, line, flow, end, endDir: east ? "right" : "left", group: L.layerGroup([casing, line, flow, end]).addTo(map) };
      const lab = end.getTooltip().getElement();               // exists now: the map has a view (set below)
      lab.addEventListener("mouseenter", () => focusGroup(g));
      lab.addEventListener("mouseleave", () => focusGroup(null));
      lab.addEventListener("click", (e) => { e.stopPropagation(); fit(focused === g ? null : g); });

      const card = document.createElement("button");
      card.type = "button"; card.className = "band";
      card.addEventListener("mouseenter", () => focusGroup(g));
      card.addEventListener("mouseleave", () => focusGroup(null));
      card.addEventListener("click", () => fit(focused === g ? null : g));
      strip.appendChild(card);
      R.card = card;
      layers.routes[g] = R;
    });

    const ends = Object.values(geo.groups).map((G) => G.end);
    geo.waypoints.filter((p) => p.kind !== "hub").forEach((p) => {
      const near = ends.find((e) => map.distance([p.lat, p.lon], [e[1], e[0]]) < 2500);
      const left = near ? near[0] >= geo.hub.lon : false;
      const m = L.circleMarker([p.lat, p.lon], { pane: "markerPane", radius: 3.5, weight: 1.5, fillOpacity: 1, className: "map-wp", interactive: false });
      m.bindTooltip(esc(p.label), { permanent: true, direction: left ? "left" : "right", offset: [left ? -6 : 6, 0], className: "map-label" });
      m.addTo(layers.wp);
    });
    // Hub: a pulsing pressure ring under a solid dot with the location name
    layers.hub.ring = L.circleMarker(hubLL, { pane: "markerPane", radius: 16, weight: 3, fillOpacity: 0.18, interactive: false, className: "map-hub-ring" });
    layers.hub.dot = L.circleMarker(hubLL, { pane: "markerPane", radius: 9, weight: 3, fillOpacity: 1, interactive: false, className: "map-hub" });
    layers.hub.dot.bindTooltip(esc(geo.hub.label), { permanent: true, direction: "right", offset: [14, 0], className: "map-label hub" });
    layers.hub.addLayer(layers.hub.ring).addLayer(layers.hub.dot).addTo(map);

    if (host.classList.contains("map-offline")) showLand();
    needFit = true;
  }

  // ------------------------------------------------------------ per-tick restyle
  function update(fg) {
    const day = fg.state.day, t = fg.state.slot;
    setTiles();
    if (landLayer) landLayer.eachLayer((l) => l.setStyle({ color: col("--map-land-edge"), fillColor: col("--map-land") }));
    layers.wp.eachLayer((m) => m.setStyle({ color: col("--surface"), fillColor: col("--ink-2") }));

    const sev = [];
    day.group_order.forEach((g) => {
      const R = layers.routes[g], G = day.groups[g], rows = G && G.rows;
      if (!R) return;
      if (!rows || !rows[t]) { R.group.remove(); R.card.hidden = true; return; }
      R.group.addTo(map); R.card.hidden = false;
      const v = fg.view(g, t), worst = worstOf(v.map((r) => r.ready));
      const idx = v[0] && v[0].idx, color = readyColor(worst);
      const w = Math.min(12, 4 + (idx || 1) * 2.2);
      R.line._fgW = w;
      R.casing.setStyle({ weight: w + 4, color: col("--surface") });
      R.line.setStyle({ weight: w, color });
      R.flow.setStyle({ weight: Math.max(2.5, w * 0.45) });
      if (R.flow._path) {                                          // faster dots = more people leaving this way
        const moving = idx != null && idx > 0.05 && worst !== "No service";
        R.flow._path.style.animationDuration = `${Math.min(4, Math.max(0.45, 1.6 / Math.max(idx || 0.4, 0.4))).toFixed(2)}s`;
        R.flow._path.style.visibility = moving ? "" : "hidden";
      }
      R.end.setStyle({ color: col("--surface"), fillColor: color });
      const idxTxt = idx == null ? "–" : `${idx.toFixed(1)}×`;
      R.end.setTooltipContent(`<span class="el-name">${esc(G.label)}</span>${fg.chip(worst)}<span class="el-idx">${idxTxt}</span>`);
      R.line.setTooltipContent(`<b>${esc(G.label)}</b> · ${esc(worst)}<br>${esc(G.routes || "")}<br>Next 30 min: ${idx == null ? "–" : idx.toFixed(1) + "× normal exit demand"}<br><span class="tip-hint">Click to zoom</span>`);
      sev.push([SEVERITY[worst] || 0, R]);

      R.card.style.setProperty("--st", `var(${READY_VAR[worst] || "--none"})`);
      R.card.setAttribute("aria-label", `${G.label}: ${worst}. Zoom map to this route group`);
      R.card.innerHTML = `<div class="band-head"><b>${esc(G.label)}</b>${fg.chip(worst)}</div>
        <div class="band-sub" title="${esc(G.routes || "")}">${esc(G.routes || "")}</div>
        <div class="band-now">Next 30 min <b>${idxTxt}</b> normal exit demand</div>
        ${bandSvg(g, fg, v)}`;
    });
    sev.sort((a, b) => a[0] - b[0]).forEach(([, R]) => { R.casing.bringToFront(); R.line.bringToFront(); });  // worst on top

    const p = day.slots[t], lvl = p && p.level;
    const ringCol = col(LEVEL_VAR[lvl] || "--accent");
    layers.hub.ring.setStyle({ color: ringCol, fillColor: ringCol, radius: 12 + Math.min(18, Math.max(0, ((p && p.pressure) || 1) - 0.5) * 7) });
    const ringEl = layers.hub.ring.getElement();
    if (ringEl) ringEl.classList.toggle("pulse", lvl === "High" || lvl === "Severe" || lvl === "Elevated");
    layers.hub.dot.setStyle({ color: col("--surface"), fillColor: col("--accent") });

    // after-hours watch (00:00–06:00 presence vs a normal night; volume only), same signal as the radar band
    const n = day.night;
    const night = !n || n.ratio == null ? ""
      : n.unusual
        ? `<div class="ah-badge unusual" title="Overnight presence 00:00–06:00 vs a normal night. Based on activity volume only.">
             <span class="dot">${fg.ICONS.bang}</span>${day.future ? "Unusual overnight presence expected" : "Unusual overnight presence"} · ${n.ratio.toFixed(1)}×</div>`
        : `<div class="ah-badge" title="Overnight presence 00:00–06:00 vs a normal night">After hours · ${n.ratio.toFixed(1)}× a normal night</div>`;
    kpiEl.innerHTML = `<div class="caption">${esc(day.poi.name)} ${day.future ? "expected" : "now"}</div>
      <div class="kpi-row"><b>${p && p.pressure != null ? p.pressure.toFixed(1) + "×" : "–"}</b>${lvl ? fg.chip(lvl) : ""}</div>
      <div class="caption">on-site crowd vs normal</div>${night}`;
    if (needFit && host.offsetWidth) { needFit = false; fit(); }
  }

  function render(fg) {
    host = host || document.getElementById("mapbox");
    strip = strip || document.getElementById("mapBands");
    if (!host || !fg.state.day) return;
    const key = fg.state.day.poi.key;                         // the loaded day's POI, so layers and data always match
    if (!window.L) { host.innerHTML = '<div class="map-msg">Map library unavailable.</div>'; return; }
    if (!geos[key]) {
      if (failed === key) return;
      if (!map) host.innerHTML = '<div class="map-msg">Loading map…</div>';
      loadGeo(key, () => render(fg));
      return;
    }
    if (!map) create(fg);
    else if (host.offsetWidth) map.invalidateSize();
    if (key !== poiKey) { geo = geos[key]; poiKey = key; buildLayers(fg); }
    update(fg);
  }

  function init() { host = document.getElementById("mapbox"); strip = document.getElementById("mapBands"); }

  return { init, render };
})();
