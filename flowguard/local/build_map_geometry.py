"""Build the FlowGuard map geometry asset (app/static/data/map.json) from the local TransLink GTFS feed.

Run from the repo root:
    python flowguard/local/build_map_geometry.py

Outputs real route polylines per route group, a Park Royal hub, labelled waypoints and a simplified
land/water silhouette. The frontend (static/map.js) projects these lat/lon rings itself, so the
geometry is stored unprojected. Route-group classification reuses fg_settings.ROUTE_GROUP_RULES and
config/route_group_overrides.csv, the same rules as pipeline/06_gtfs_service, so the map's lines
match the readiness groups exactly.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
FLOWGUARD = os.path.abspath(os.path.join(HERE, ".."))
GTFS_DIR = os.path.join(HERE, "data", "gtfs")
CONFIG_DIR = os.path.join(FLOWGUARD, "config")
PIPELINE_DIR = os.path.join(FLOWGUARD, "pipeline")
OUT = os.path.join(FLOWGUARD, "app", "static", "data", "map.json")

sys.path.insert(0, PIPELINE_DIR)
import fg_settings as S  # noqa: E402

NE_LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson"
RDP_TOL = 0.0006          # degrees (~65 m) — keeps lines light while staying recognisable
MAX_SHAPES_PER_GROUP = 3
MIN_LAND_AREA = 1e-5      # drop slivers after clipping
GROUP_COLOR = {"EASTBOUND": "#3987e5", "DOWNTOWN": "#8b5cf6", "WEST_VAN_LOCAL": "#14b8a6"}

# Labelled anchors shown on the map (hand-placed near the real locations).
WAYPOINTS = [
    {"label": "Park Royal", "lat": 49.3265, "lon": -123.1380, "kind": "hub"},
    {"label": "Lions Gate Bridge", "lat": 49.3155, "lon": -123.1385, "kind": "way"},
    {"label": "Downtown Vancouver", "lat": 49.2827, "lon": -123.1207, "kind": "way"},
    {"label": "UBC", "lat": 49.2606, "lon": -123.2460, "kind": "way"},
    {"label": "Lonsdale Quay", "lat": 49.3103, "lon": -123.0824, "kind": "way"},
    {"label": "Phibbs Exchange", "lat": 49.3149, "lon": -123.0289, "kind": "way"},
    {"label": "Metrotown", "lat": 49.2276, "lon": -123.0000, "kind": "way"},
    {"label": "Horseshoe Bay", "lat": 49.3994, "lon": -123.2725, "kind": "way"},
    {"label": "Dundarave", "lat": 49.3355, "lon": -123.1830, "kind": "way"},
]

# Rough fallback silhouette if the Natural Earth download fails (stylised, not survey-accurate).
FALLBACK_LAND = [
    [[-123.34, 49.41], [-122.90, 49.41], [-122.90, 49.34], [-123.05, 49.32],
     [-123.12, 49.30], [-123.20, 49.30], [-123.26, 49.33], [-123.34, 49.36]],
    [[-123.34, 49.28], [-123.24, 49.24], [-123.10, 49.19], [-122.90, 49.19], [-122.90, 49.28]],
]


# ---------------------------------------------------------------- geometry helpers
def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def perp_dist(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def rdp(points, tol):
    """Iterative Douglas–Peucker; points are (lon, lat)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        worst, idx = 0.0, -1
        for k in range(i + 1, j):
            d = perp_dist(points[k], points[i], points[j])
            if d > worst:
                worst, idx = d, k
        if worst > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def area(ring):
    a = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def clip_rect(points, bbox):
    """Sutherland–Hodgman clip of a polygon ring to a rectangle (convex clip region)."""
    minx, miny, maxx, maxy = bbox

    def clip_edge(pts, inside, intersect):
        out = []
        n = len(pts)
        for i in range(n):
            cur, prev = pts[i], pts[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(intersect(prev, cur))
        return out

    def ix_vertical(p, q, x):
        t = (x - p[0]) / (q[0] - p[0])
        return [x, p[1] + t * (q[1] - p[1])]

    def ix_horizontal(p, q, y):
        t = (y - p[1]) / (q[1] - p[1])
        return [p[0] + t * (q[0] - p[0]), y]

    pts = points
    for x in (minx, maxx):
        pts = clip_edge(pts, lambda p, x=x: p[0] >= x, lambda p, q, x=x: ix_vertical(p, q, x))
    for y in (miny, maxy):
        pts = clip_edge(pts, lambda p, y=y: p[1] >= y, lambda p, q, y=y: ix_horizontal(p, q, y))
    return pts


# ---------------------------------------------------------------- data loading
def read_csv(name):
    with open(os.path.join(GTFS_DIR, name), newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_overrides():
    out = {}
    path = os.path.join(CONFIG_DIR, "route_group_overrides.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                hs = (row.get("trip_headsign") or "").strip().lower()
                if hs:
                    out[hs] = (row.get("route_group") or "").strip()
    return out


def classify(headsign, overrides):
    hs = (headsign or "").strip().lower()
    if hs in overrides:
        return overrides[hs]
    for needle, group in S.ROUTE_GROUP_RULES:
        if needle in hs:
            return group
    return S.ROUTE_GROUP_DEFAULT


def build_routes():
    stops = read_csv("stops.txt")
    pr_stops = {
        r["stop_id"]
        for r in stops
        if r.get("stop_lat") and r.get("stop_lon")
        and haversine_m(S.POI_LAT, S.POI_LON, float(r["stop_lat"]), float(r["stop_lon"])) <= S.GTFS_STOP_RADIUS_M
    }
    print(f"Park Royal stops within {S.GTFS_STOP_RADIUS_M} m: {len(pr_stops)}")

    pr_trips = set()
    with open(os.path.join(GTFS_DIR, "stop_times.txt"), newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["stop_id"] in pr_stops:
                pr_trips.add(row["trip_id"])
    print(f"trips calling at Park Royal: {len(pr_trips):,}")

    trips = {r["trip_id"]: r for r in read_csv("trips.txt") if r["trip_id"] in pr_trips}
    routes = {r["route_id"]: r for r in read_csv("routes.txt")}
    overrides = load_overrides()

    group_shapes = {}
    for t in trips.values():
        group = classify(t.get("trip_headsign"), overrides)
        if group == "EXCLUDE":
            continue
        group_shapes.setdefault(group, {})
        sid = t.get("shape_id")
        if sid:
            group_shapes[group][sid] = group_shapes[group].get(sid, 0) + 1

    wanted = set()
    chosen = {}
    for group, counts in group_shapes.items():
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_SHAPES_PER_GROUP]
        chosen[group] = [sid for sid, _ in top]
        wanted.update(chosen[group])

    shape_pts = {sid: [] for sid in wanted}
    with open(os.path.join(GTFS_DIR, "shapes.txt"), newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sid = row["shape_id"]
            if sid in shape_pts:
                shape_pts[sid].append((int(row["shape_pt_sequence"]),
                                       float(row["shape_pt_lon"]), float(row["shape_pt_lat"])))

    def orient(pts):
        """Start the polyline at the end nearest the hub so timelines run outward."""
        d0 = haversine_m(S.POI_LAT, S.POI_LON, pts[0][1], pts[0][0])
        d1 = haversine_m(S.POI_LAT, S.POI_LON, pts[-1][1], pts[-1][0])
        return pts if d0 <= d1 else pts[::-1]

    groups_out = {}
    for group in S.ROUTE_GROUPS:
        lines = []
        for sid in chosen.get(group, []):
            pts = [(lon, lat) for _, lon, lat in sorted(shape_pts[sid])]
            pts = orient(rdp(pts, RDP_TOL))
            if len(pts) >= 2:
                lines.append([[round(lon, 5), round(lat, 5)] for lon, lat in pts])
        groups_out[group] = {
            "label": S.GROUP_LABELS.get(group, group),
            "corridors": S.GROUP_CORRIDORS.get(group, []),
            "color": GROUP_COLOR.get(group, "#888888"),
            "lines": lines,
        }
        print(f"  {group}: {len(lines)} line(s), {sum(len(l) for l in lines)} points")
    return groups_out


def fetch(url):
    """Fetch bytes; urllib first, then curl (macOS framework Python often lacks CA certs)."""
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except Exception:
        import subprocess
        return subprocess.check_output(["curl", "-sL", url], timeout=120)


def build_land(bbox):
    try:
        geo = json.loads(fetch(NE_LAND_URL).decode("utf-8"))
        rings = []
        for feat in geo.get("features", []):
            g = feat.get("geometry") or {}
            polys = [g.get("coordinates")] if g.get("type") == "Polygon" else (g.get("coordinates") or [])
            for poly in polys:
                for ring in poly[:1]:                      # exterior only; holes ignored (stylised)
                    pts = [[float(x), float(y)] for x, y in ring]
                    minx = min(p[0] for p in pts); maxx = max(p[0] for p in pts)
                    miny = min(p[1] for p in pts); maxy = max(p[1] for p in pts)
                    if maxx < bbox[0] or minx > bbox[2] or maxy < bbox[1] or miny > bbox[3]:
                        continue
                    clipped = clip_rect(pts, bbox)
                    if len(clipped) < 3 or area(clipped) < MIN_LAND_AREA:
                        continue
                    simple = rdp(clipped, RDP_TOL)
                    if len(simple) >= 3:
                        rings.append([[round(x, 5), round(y, 5)] for x, y in simple])
        print(f"land rings from Natural Earth: {len(rings)}")
        return rings, "Land: Natural Earth 1:50m (public domain). Routes: TransLink GTFS."
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Natural Earth fetch failed ({type(e).__name__}: {e}); using fallback silhouette.")
        return FALLBACK_LAND, "Stylised silhouette. Routes: TransLink GTFS."


def main():
    groups = build_routes()

    xs, ys = [], []
    for g in groups.values():
        for line in g["lines"]:
            xs += [p[0] for p in line]
            ys += [p[1] for p in line]
    for w in WAYPOINTS:
        xs.append(w["lon"]); ys.append(w["lat"])
    pad_x = (max(xs) - min(xs)) * 0.06 or 0.02
    pad_y = (max(ys) - min(ys)) * 0.06 or 0.02
    bbox = [round(min(xs) - pad_x, 5), round(min(ys) - pad_y, 5),
            round(max(xs) + pad_x, 5), round(max(ys) + pad_y, 5)]

    land, attribution = build_land(bbox)
    payload = {
        "bbox": bbox,
        "hub": {"label": S.POI_NAME, "lat": S.POI_LAT, "lon": S.POI_LON},
        "waypoints": WAYPOINTS,
        "land": land,
        "groups": groups,
        "group_order": S.ROUTE_GROUPS,
        "attribution": attribution,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"✅ wrote {OUT} ({os.path.getsize(OUT)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
