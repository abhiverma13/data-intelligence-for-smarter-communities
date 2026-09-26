"""Build the FlowGuard map geometry assets (app/static/data/<poi>/map.json) from the local TransLink GTFS feed.

Run from the repo root:
    python flowguard/local/build_map_geometry.py

For every point of interest in app/server/pois.json it writes real route polylines per route group, the hub,
labelled waypoints, a band corner per group and a simplified land silhouette (the offline fallback under the
Leaflet basemap). Departing trips and their route groups come from fg_core.poi_departures, the same function
pipeline/06_gtfs_service uses, so the map's lines match the readiness groups exactly.
"""
from __future__ import annotations

import csv
import itertools
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FLOWGUARD = os.path.abspath(os.path.join(HERE, ".."))
GTFS_DIR = os.path.join(HERE, "data", "gtfs")
CONFIG_DIR = os.path.join(FLOWGUARD, "config")
PIPELINE_DIR = os.path.join(FLOWGUARD, "pipeline")
OUT_DIR = os.path.join(FLOWGUARD, "app", "static", "data")

sys.path.insert(0, PIPELINE_DIR)
import fg_core  # noqa: E402
import fg_settings as S  # noqa: E402

NE_LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson"
RDP_TOL = 0.0004          # degrees (~45 m) — keeps lines light while staying recognisable
MAX_ROUTES_PER_GROUP = 4  # busiest routes per group (capacity-weighted departures), one shape each
MIN_LAND_AREA = 1e-5      # drop slivers after clipping
CORNERS = {"topleft": (-1, 1), "topright": (1, 1), "bottomleft": (-1, -1), "bottomright": (1, -1)}
TOPRIGHT_PENALTY = 0.4    # the hub KPI and zoom controls live top-right; prefer other corners for bands

# Per POI: how far from the hub lines are drawn (long rail/express lines are cut there) and hand-placed labels.
MAP = {
    "park_royal": {"radius_km": 15, "waypoints": [
        ("Lions Gate Bridge", 49.3155, -123.1385), ("Downtown Vancouver", 49.2827, -123.1207),
        ("Lonsdale Quay", 49.3103, -123.0824), ("Phibbs Exchange", 49.3149, -123.0289),
        ("Metrotown", 49.2276, -123.0000), ("Horseshoe Bay", 49.3994, -123.2725), ("Dundarave", 49.3355, -123.1830)]},
    "ubc": {"radius_km": 19, "waypoints": [
        ("Downtown Vancouver", 49.2827, -123.1207), ("Kitsilano", 49.2684, -123.1683),
        ("Commercial–Broadway", 49.2626, -123.0690), ("Kerrisdale", 49.2344, -123.1553),
        ("Joyce–Collingwood", 49.2383, -123.0318), ("Metrotown", 49.2276, -123.0000)]},
    "waterfront": {"radius_km": 14, "waypoints": [
        ("Lonsdale Quay", 49.3103, -123.0824), ("Stanley Park", 49.3017, -123.1417),
        ("Commercial–Broadway", 49.2626, -123.0690), ("Metrotown", 49.2276, -123.0000),
        ("Oakridge–41st", 49.2334, -123.1162), ("YVR Airport", 49.1967, -123.1815),
        ("Kitsilano", 49.2684, -123.1683), ("UBC", 49.2606, -123.2460)]},
}


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
def gtfs(name, cols):
    return pd.read_csv(os.path.join(GTFS_DIR, f"{name}.txt"), usecols=cols, dtype=str, encoding="utf-8-sig")


def load_feed():
    stops = gtfs("stops", ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"])
    stops[["stop_lat", "stop_lon"]] = stops[["stop_lat", "stop_lon"]].astype(float)
    stop_times = gtfs("stop_times", ["trip_id", "stop_id", "stop_sequence", "departure_time"])
    trips = gtfs("trips", ["trip_id", "route_id", "service_id", "trip_headsign", "shape_id"])
    routes = gtfs("routes", ["route_id", "route_short_name", "route_long_name", "route_type"])
    print(f"stop_times rows: {len(stop_times):,}")
    return stops, stop_times, trips, routes, fg_core.last_stops(stop_times)


def load_overrides():
    ov = pd.read_csv(os.path.join(CONFIG_DIR, "route_group_overrides.csv"), dtype=str).fillna("")
    return {poi: dict(zip(g["trip_headsign"], g["route_group"])) for poi, g in ov.groupby("poi")}


def load_shapes(wanted):
    pts = {sid: [] for sid in wanted}
    with open(os.path.join(GTFS_DIR, "shapes.txt"), newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["shape_id"] in pts:
                pts[row["shape_id"]].append((int(row["shape_pt_sequence"]), float(row["shape_pt_lon"]), float(row["shape_pt_lat"])))
    return {sid: [(lon, lat) for _, lon, lat in sorted(p)] for sid, p in pts.items()}


def pick_shapes(dep, trips):
    """Per route group: the most common shape of each of its busiest routes (capacity-weighted departures)."""
    d = dep[dep["route_group"] != "EXCLUDE"].merge(trips[["trip_id", "shape_id"]], on="trip_id")
    out = {}
    for group, g in d.groupby("route_group"):
        weight = g.groupby("route_name")["capacity"].sum().sort_values(ascending=False)
        out[group] = [g[g["route_name"] == r]["shape_id"].value_counts().index[0] for r in weight.index[:MAX_ROUTES_PER_GROUP]]
    return out


def outbound(pts, lat0, lon0, radius_m):
    """The part of a trip's shape after the POI (nearest point to the hub), cut where it leaves radius_m."""
    near = min(range(len(pts)), key=lambda i: haversine_m(lat0, lon0, pts[i][1], pts[i][0]))
    out = []
    for lon, lat in pts[near:]:
        out.append((lon, lat))
        if haversine_m(lat0, lon0, lat, lon) > radius_m:
            break
    return out


def assign_corners(ends, lat0, lon0):
    """Band corner per group: the corner assignment that best matches each group's outward direction."""
    keys = list(ends)
    vec = {}
    for g in keys:
        dx = (ends[g][0] - lon0) * math.cos(math.radians(lat0)); dy = ends[g][1] - lat0
        n = math.hypot(dx, dy) or 1.0
        vec[g] = (dx / n, dy / n)
    best, best_cost = None, float("inf")
    for combo in itertools.permutations(CORNERS, len(keys)):
        cost = 0.0
        for g, c in zip(keys, combo):
            cx, cy = CORNERS[c]
            cost -= (vec[g][0] * cx + vec[g][1] * cy) / math.sqrt(2)
            cost += TOPRIGHT_PENALTY if c == "topright" else 0.0
        if cost < best_cost:
            best, best_cost = dict(zip(keys, combo)), cost
    return best


def build_poi(poi, feed, overrides, shapes_cache):
    stops, stop_times, trips, routes, last = feed
    cfg = MAP.get(poi["key"], {"radius_km": 15, "waypoints": []})
    radius = cfg["radius_km"] * 1000
    lat0, lon0 = poi["lat"], poi["lon"]
    dep, near = fg_core.poi_departures(stops, stop_times, trips, routes, last, overrides.get(poi["key"], {}), poi)
    chosen = pick_shapes(dep, trips)
    shapes = shapes_cache(set(itertools.chain.from_iterable(chosen.values())))
    print(f"\n{poi['name']}: {len(near)} stops, {len(dep):,} departures")

    groups, ends = {}, {}
    for g in poi["groups"]:
        lines = []
        for sid in chosen.get(g["key"], []):
            pts = rdp(outbound(shapes[sid], lat0, lon0, radius), RDP_TOL)
            if len(pts) >= 2:
                lines.append([[round(lon, 5), round(lat, 5)] for lon, lat in pts])
        if lines:   # far end of the longest line: where the group's label and band direction point
            far = max(lines, key=lambda l: haversine_m(lat0, lon0, l[-1][1], l[-1][0]))[-1]
            ends[g["key"]] = far
        groups[g["key"]] = {"label": g["label"], "routes": g["routes"], "corridors": g["corridors"], "lines": lines,
                            "end": ends.get(g["key"])}
        print(f"  {g['key']}: {len(lines)} line(s), {sum(len(l) for l in lines)} points  ({', '.join(chosen.get(g['key'], [])) or 'no shapes'})")
    for k, c in assign_corners(ends, lat0, lon0).items():
        groups[k]["corner"] = c

    wps = [{"label": poi["name"], "lat": lat0, "lon": lon0, "kind": "hub"}]
    wps += [{"label": l, "lat": la, "lon": lo, "kind": "way"} for l, la, lo in cfg["waypoints"]
            if haversine_m(lat0, lon0, la, lo) <= radius * 1.1]

    xs = [p[0] for g in groups.values() for l in g["lines"] for p in l] + [w["lon"] for w in wps]
    ys = [p[1] for g in groups.values() for l in g["lines"] for p in l] + [w["lat"] for w in wps]
    pad_x = (max(xs) - min(xs)) * 0.06 or 0.02
    pad_y = (max(ys) - min(ys)) * 0.06 or 0.02
    bbox = [round(min(xs) - pad_x, 5), round(min(ys) - pad_y, 5), round(max(xs) + pad_x, 5), round(max(ys) + pad_y, 5)]
    land, attribution = build_land(bbox)
    return {
        "poi": poi["key"],
        "bbox": bbox,
        "hub": {"label": poi["name"], "lat": lat0, "lon": lon0},
        "waypoints": wps,
        "land": land,
        "groups": groups,
        "group_order": S.groups(poi),
        "attribution": attribution,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


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
    feed = load_feed()
    overrides = load_overrides()
    cache = {}

    def shapes_cache(wanted):
        missing = wanted - cache.keys()
        if missing:
            cache.update(load_shapes(missing))
        return cache

    for poi in S.POIS:
        payload = build_poi(poi, feed, overrides, shapes_cache)
        out = os.path.join(OUT_DIR, poi["key"], "map.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        print(f"✅ wrote {out} ({os.path.getsize(out)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
