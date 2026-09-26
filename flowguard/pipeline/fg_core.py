"""FlowGuard analytics core: every formula from spec §5.2, in plain pandas/numpy.

Imported by the Databricks notebooks (04–07) *and* by local scripts, so both compute
identical numbers. Spark only does the heavy lifting (aggregating millions of visits to slots);
everything here works on small slot-level frames (≈15k slots per point of interest).

Every function works on ONE point of interest (POI) at a time; `poi` is its entry from
app/server/pois.json (via fg_settings.POI_BY_KEY). Outputs carry a `poi` column.

Conventions: timestamps are naive pandas datetimes in Vancouver local time; the slot grid
has no gaps; `dow` is 0=Mon … 6=Sun; `sod` is slot-of-day 0..47.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import fg_settings as S

SLOT = pd.Timedelta(minutes=S.SLOT_MINUTES)


# ---------------------------------------------------------------- helpers
def make_grid() -> pd.DatetimeIndex:
    end = pd.Timestamp(S.DATA_END) + pd.Timedelta(days=1) - SLOT
    return pd.date_range(S.DATA_START, end, freq=f"{S.SLOT_MINUTES}min")


def slot_of_day(ts) -> np.ndarray:
    ts = pd.DatetimeIndex(ts)
    return np.asarray(ts.hour * (60 // S.SLOT_MINUTES) + ts.minute // S.SLOT_MINUTES)


def level(value: float, levels) -> str | None:
    if value is None or not np.isfinite(value):
        return None
    for upper, name in levels:
        if value < upper:
            return name
    return levels[-1][1]


def _typical(values: np.ndarray, dow: np.ndarray, sod: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """Median of `values` per (dow, sod), computed on `mask` rows, mapped back to every row."""
    m = np.ones(len(values), bool) if mask is None else mask
    med = pd.Series(values[m]).groupby([dow[m], sod[m]]).median()
    return med.reindex(pd.MultiIndex.from_arrays([dow, sod])).to_numpy(dtype=float)


def _baseline(values: np.ndarray, grid: pd.DatetimeIndex, weeks: int | None) -> np.ndarray:
    """"Normal" for each slot: median for the same weekday × slot — over the whole history, or, if `weeks`
    is set (seasonal POIs such as UBC term vs summer), over the surrounding ±`weeks` weeks only."""
    dow, sod = np.asarray(grid.dayofweek), slot_of_day(grid)
    if not weeks:
        return _typical(values, dow, sod)
    week = np.asarray((grid.normalize() - grid.normalize()[0]).days // 7)
    key = dow * S.SLOTS_PER_DAY + sod
    m = pd.DataFrame({"w": week, "k": key, "v": values}).pivot_table(index="w", columns="k", values="v", aggfunc="first", dropna=False)
    med = m.rolling(2 * weeks + 1, center=True, min_periods=weeks + 1).median()
    return med.to_numpy(dtype=float)[med.index.get_indexer(week), med.columns.get_indexer(key)]


def _floor(baseline: np.ndarray, share: float) -> np.ndarray:
    return np.maximum(baseline, share * np.nanmax(baseline))


def service_day_types(dates: pd.DatetimeIndex, ref_day: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """TransLink service day type and label per date (holiday overrides from ref_day_type)."""
    ref = ref_day.assign(date=pd.to_datetime(ref_day["date"])).set_index("date")
    dates = pd.DatetimeIndex(dates).normalize()
    default = np.where(dates.dayofweek == 5, "SATURDAY", np.where(dates.dayofweek == 6, "SUNDAY_HOLIDAY", "WEEKDAY"))
    sdt = ref["service_day_type"].reindex(dates).fillna(pd.Series(default, index=dates)).to_numpy()
    label = ref["label"].reindex(dates).fillna("").to_numpy()
    return sdt, label


# ---------------------------------------------------------------- 04: slots, baselines, pressure, signature, days
def build_slots(slot_agg: pd.DataFrame, corr_agg: pd.DataFrame, ref_day: pd.DataFrame, poi: dict):
    """
    slot_agg: index slot_ts · arrivals, departures, arrivals_occ, departures_occ, median_dwell
    corr_agg: long frame slot_ts, corridor, arrivals, departures
    Returns (slots, slot_corridor, days), each with a `poi` column.
    """
    corridors = S.corridors(poi)
    weeks = poi.get("baseline_weeks")
    grid = make_grid()
    a = slot_agg.reindex(grid).fillna({"arrivals": 0, "departures": 0, "arrivals_occ": 0, "departures_occ": 0})
    dow = np.asarray(grid.dayofweek)
    sod = slot_of_day(grid)
    out = pd.DataFrame(index=grid)
    out["poi"] = poi["key"]
    out["slot_ts"] = grid
    out["date"] = grid.normalize()
    out["day_type"] = np.array(S.DAY_TYPES)[dow]
    out["slot_of_day"] = sod
    out["service_day_type"], _ = service_day_types(grid, ref_day)
    out["arrivals"] = a["arrivals"].astype(int).to_numpy()
    out["departures"] = a["departures"].astype(int).to_numpy()

    # 1. occupancy = cumulative arrivals − cumulative departures (long stays excluded: multi-day devices)
    arr_o = a["arrivals_occ" if S.OCC_EXCLUDE_LONG_STAY else "arrivals"].to_numpy(float)
    dep_o = a["departures_occ" if S.OCC_EXCLUDE_LONG_STAY else "departures"].to_numpy(float)
    out["occupancy"] = (np.cumsum(arr_o) - np.cumsum(dep_o)).astype(int)

    # 2. pressure = occupancy / median occupancy for the same weekday × slot
    base_occ = _baseline(out["occupancy"].to_numpy(float), grid, weeks)
    out["baseline_occ"] = base_occ
    out["pressure"] = out["occupancy"] / _floor(base_occ, S.BASELINE_OCC_FLOOR_SHARE)
    out["pressure_level"] = [level(p, S.PRESSURE_LEVELS) for p in out["pressure"]]
    out["baseline_arrivals"] = _baseline(out["arrivals"].to_numpy(float), grid, weeks)
    out["baseline_departures"] = _baseline(out["departures"].to_numpy(float), grid, weeks)
    out["median_dwell"] = a["median_dwell"].to_numpy(float)

    # corridor frame + trailing-2 h arrival shares (the catchment)
    ca = corr_agg.pivot_table(index="slot_ts", columns="corridor", values="arrivals", aggfunc="sum")
    ca = ca.reindex(index=grid, columns=corridors).fillna(0)
    cd = corr_agg.pivot_table(index="slot_ts", columns="corridor", values="departures", aggfunc="sum")
    cd = cd.reindex(index=grid, columns=corridors).fillna(0)
    trail = ca.rolling(S.TRAILING_SLOTS, min_periods=1).sum()
    shares = trail.div(trail.sum(axis=1).replace(0, np.nan), axis=0)
    corr_rows = []
    for c in corridors:
        dep_c = cd[c].to_numpy(float)
        share_c = shares[c].to_numpy(float)
        corr_rows.append(pd.DataFrame({
            "poi": poi["key"], "slot_ts": grid, "corridor": c,
            "arrivals": ca[c].astype(int).to_numpy(), "departures": dep_c.astype(int),
            "baseline_departures": _baseline(dep_c, grid, weeks),
            "share": share_c, "baseline_share": _baseline(share_c, grid, weeks),
        }))
    slot_corridor = pd.concat(corr_rows, ignore_index=True)

    # 8. mobility signature on the trailing 2 h of arrivals
    local = shares[poi["local_corridors"]].sum(axis=1).to_numpy()
    out["local_share"] = local
    out["baseline_local_share"] = _baseline(local, grid, weeks)
    regional = shares[poi["regional_corridors"]].sum(axis=1).to_numpy()
    out["regional_share"] = regional
    out["baseline_regional_share"] = _baseline(regional, grid, weeks)
    w = a["arrivals"].to_numpy(float)
    md = np.nan_to_num(out["median_dwell"].to_numpy(float))
    num = pd.Series(md * w).rolling(S.TRAILING_SLOTS, min_periods=1).sum().to_numpy()
    den = pd.Series(w).rolling(S.TRAILING_SLOTS, min_periods=1).sum().replace(0, np.nan).to_numpy()
    trailing_dwell = num / den
    out["trailing_dwell"] = trailing_dwell
    out["stay_ratio"] = trailing_dwell / _baseline(trailing_dwell, grid, weeks)
    visitor = shares["OUT_OF_REGION"].to_numpy()
    out["visitor_ratio"] = visitor / _baseline(visitor, grid, weeks)
    out["signature"] = [
        signature(p, r, br, s, v)
        for p, r, br, s, v in zip(out["pressure"], regional, out["baseline_regional_share"], out["stay_ratio"], out["visitor_ratio"])
    ]

    # 3. days: surge ratio vs median for that weekday
    days = out.groupby("date").agg(arrivals=("arrivals", "sum")).reset_index()
    days.insert(0, "poi", poi["key"])
    days["day_type"] = np.array(S.DAY_TYPES)[days["date"].dt.dayofweek]
    if weeks:   # seasonal: vs the same weekday in the surrounding ±weeks weeks
        usual = days.groupby("day_type")["arrivals"].transform(
            lambda x: x.rolling(2 * weeks + 1, center=True, min_periods=weeks + 1).median())
    else:
        usual = days.groupby("day_type")["arrivals"].transform("median")
    days["surge_ratio"] = days["arrivals"] / usual
    days["is_surge"] = days["surge_ratio"] >= poi.get("surge_threshold", S.SURGE_RATIO_THRESHOLD)
    days["service_day_type"], ref_label = service_day_types(pd.DatetimeIndex(days["date"]), ref_day)
    lo, hi = S.PEAK_WINDOW_SLOTS
    day_win = out[(out["slot_of_day"] >= lo) & (out["slot_of_day"] < hi)]
    peak_idx = day_win.groupby("date")["pressure"].idxmax()
    days["peak_pressure"] = day_win.loc[peak_idx.to_numpy(), "pressure"].to_numpy()
    days["peak_pressure_slot"] = day_win.loc[peak_idx.to_numpy(), "slot_ts"].to_numpy()
    days["label"] = [
        lbl if lbl else ("Surge day" if surge else "")
        for lbl, surge in zip(ref_label, days["is_surge"])
    ]

    return out.reset_index(drop=True), slot_corridor, days


def signature(pressure, regional, base_regional, stay_ratio, visitor_ratio) -> str:
    if pressure is None or not np.isfinite(pressure) or pressure < S.PRESSURE_LEVELS[0][0]:
        return "Routine activity"
    q = []
    if np.isfinite(regional) and np.isfinite(base_regional) and regional >= base_regional + S.SIGNATURE_REGIONAL_PP:
        q.append("Regional")
    if np.isfinite(stay_ratio) and stay_ratio >= S.SIGNATURE_STAY_LONG:
        q.append("Extended-stay")
    elif np.isfinite(stay_ratio) and stay_ratio <= S.SIGNATURE_STAY_SHORT:
        q.append("Quick-turnover")
    if np.isfinite(visitor_ratio) and visitor_ratio >= S.SIGNATURE_VISITOR_X:
        q.append("Visitor-heavy")
    return (" + ".join(q) + " surge") if q else "General surge"


# ---------------------------------------------------------------- 05: egress kernel, forecast, backtest
def estimate_kernel(k_counts: pd.Series, poi: dict) -> pd.DataFrame:
    """k_counts: number of training visits per egress_k (slots between arrival and departure slot).
    P(k) = share of visitors leaving k slots after their arrival slot; mass beyond K never returns."""
    total = k_counts.sum()
    k = np.arange(S.KERNEL_MAX_K + 1)
    p = k_counts.reindex(k, fill_value=0).to_numpy(float) / total
    return pd.DataFrame({"poi": poi["key"], "k": k, "p": p, "cum_p": np.cumsum(p)})


def today_factor(arrivals: np.ndarray, typical_arrivals: np.ndarray) -> np.ndarray:
    """How busy today is so far: trailing-2 h arrivals ÷ typical trailing-2 h arrivals (clipped)."""
    a = pd.Series(arrivals).rolling(S.TRAILING_SLOTS, min_periods=1).sum()
    t = pd.Series(typical_arrivals).rolling(S.TRAILING_SLOTS, min_periods=1).sum()
    lo, hi = S.TODAY_FACTOR_CLIP
    return np.clip((a / t.replace(0, np.nan)).fillna(1.0).to_numpy(), lo, hi)


def egress_forecast(arrivals: np.ndarray, typical_arrivals: np.ndarray, p: np.ndarray, h: int,
                    scaling: bool | None = None) -> np.ndarray:
    """dep_hat[τ] for target slot τ, forecast made at origin t = τ − h (spec §5.2.4):
       Σ_{i ≤ t} arrivals(i)·P(τ−i)  +  Σ_{t < i ≤ τ} typical_arrivals(i)·f(t)·P(τ−i)
    where f(t) = today_factor at the origin slot (1 if TODAY_SCALING is off): arrivals still to come
    follow the typical pattern, scaled by how busy today has been so far.
    Returns an array aligned to the *target* slot; the first K entries are NaN (not enough history)."""
    n = len(arrivals)
    scaling = S.TODAY_SCALING if scaling is None else scaling
    f = today_factor(arrivals, typical_arrivals) if scaling else np.ones(n)
    f_origin = np.concatenate([np.ones(h), f[: n - h]])      # f(t) indexed by target τ = t + h
    out = np.zeros(n)
    for j, pj in enumerate(p):
        if j >= h:
            out[j:] += pj * arrivals[: n - j]
        else:
            out[j:] += pj * typical_arrivals[: n - j] * f_origin[j:]
    out[: len(p) - 1] = np.nan
    return out


def run_forecasts(slots: pd.DataFrame, slot_corridor: pd.DataFrame, kernel: pd.DataFrame, poi: dict):
    """Returns (egress_forecast long frame, model_backtest frame) for corridor 'ALL' + each corridor."""
    p = kernel.sort_values("k")["p"].to_numpy()
    slots = slots.sort_values("slot_ts").reset_index(drop=True)
    grid = pd.DatetimeIndex(slots["slot_ts"])
    dow = np.asarray(grid.dayofweek)
    sod = slots["slot_of_day"].to_numpy()
    train = np.asarray(grid < pd.Timestamp(S.TRAIN_END))
    n = len(grid)

    series = {"ALL": (slots["arrivals"].to_numpy(float), slots["departures"].to_numpy(float),
                      slots["baseline_departures"].to_numpy(float))}
    for c, g in slot_corridor.groupby("corridor"):
        g = g.set_index("slot_ts").reindex(grid)
        series[c] = (g["arrivals"].to_numpy(float), g["departures"].to_numpy(float), g["baseline_departures"].to_numpy(float))

    rows, backtest = [], []
    for c, (arr, dep, base_dep) in series.items():
        typ_arr = _typical(arr, dow, sod, train)          # typical arrivals learned on training data only
        base_dep_f = _floor(base_dep, S.BASELINE_OCC_FLOOR_SHARE)
        for h in S.HORIZONS:
            pred = egress_forecast(arr, typ_arr, p, h)    # aligned to target slot τ
            tgt = np.arange(h, n)                          # τ with a valid origin t = τ − h
            rows.append(pd.DataFrame({
                "poi": poi["key"], "origin_slot_ts": grid[tgt - h], "horizon": h, "target_slot_ts": grid[tgt],
                "corridor": c, "dep_hat": pred[tgt], "dep_actual": dep[tgt].astype(int),
                "egress_idx": pred[tgt] / base_dep_f[tgt],
            }))
            if c == "ALL":
                m = ~train & ~np.isnan(pred)
                m[:h] = False
                backtest.append(_metrics(poi, h, "egress", "departures", dep[m], pred[m]))
                static = egress_forecast(arr, typ_arr, p, h, scaling=False)
                backtest.append(_metrics(poi, h, "egress_static", "departures", dep[m], static[m]))
                typ_dep = _typical(dep, dow, sod, train)
                backtest.append(_metrics(poi, h, "typical_week", "departures", dep[m], typ_dep[m]))
    fc = pd.concat(rows, ignore_index=True).dropna(subset=["dep_hat"])
    return fc, pd.DataFrame(backtest)


def _metrics(poi, h, model, target, y, yhat) -> dict:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return {"poi": poi["key"], "horizon": h, "model": model, "target": target, "r2": 1 - ss_res / ss_tot,
            "mae": float(np.mean(np.abs(y - yhat))), "n": int(len(y))}


def gbt_arrival_backtest(slots: pd.DataFrame, poi: dict) -> pd.DataFrame:
    """Gradient-boosted arrival forecaster (lags + calendar) vs the typical-week pattern.
    Included for comparison with the egress model; arrivals are not a product feature."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    slots = slots.sort_values("slot_ts").reset_index(drop=True)
    grid = pd.DatetimeIndex(slots["slot_ts"])
    arr = slots["arrivals"].astype(float).reset_index(drop=True)
    train = pd.Series(grid < pd.Timestamp(S.TRAIN_END))
    X = pd.DataFrame({"dow": grid.dayofweek, "sod": slots["slot_of_day"].to_numpy()})
    for lag in [0, 1, 2, 3, 47, 335]:          # lag 0 = arrivals in the current slot (known at t)
        X[f"lag{lag}"] = arr.shift(lag)
    dow, sod = np.asarray(grid.dayofweek), slots["slot_of_day"].to_numpy()
    typ = _typical(arr.to_numpy(), dow, sod, train.to_numpy())
    rows = []
    for h in [1, 2]:
        y = arr.shift(-h)
        ok = X.notna().all(axis=1) & y.notna()
        model = HistGradientBoostingRegressor(max_iter=300, random_state=0).fit(X[ok & train], y[ok & train])
        te = ok & ~train
        rows.append(_metrics(poi, h, "gbt_arrivals", "arrivals", y[te], model.predict(X[te])))
        rows.append(_metrics(poi, h, "typical_week", "arrivals", y[te], np.roll(typ, -h)[te.to_numpy()]))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 06: GTFS route groups + scheduled service
def haversine_m(lat, lon, lat0, lon0):
    lat, lon = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    lat0, lon0 = np.radians(lat0), np.radians(lon0)
    a = np.sin((lat - lat0) / 2) ** 2 + np.cos(lat0) * np.cos(lat) * np.sin((lon - lon0) / 2) ** 2
    return 2 * 6371000 * np.arcsin(np.sqrt(a))


def last_stops(stop_times: pd.DataFrame) -> pd.DataFrame:
    """Final stop of every trip (computed once, shared by all POIs)."""
    st = stop_times.assign(seq=stop_times["stop_sequence"].astype(int))
    return st.loc[st.groupby("trip_id")["seq"].idxmax(), ["trip_id", "stop_id"]]


def poi_departures(stops, stop_times, trips, routes, last, overrides: dict, poi: dict):
    """One row per scheduled trip that *leaves* the POI: its first stop within stop_radius_m,
    excluding trips that end within terminating_radius_m (those arrive rather than depart).
    Returns (departures, near_stops)."""
    stops = stops.assign(dist_m=haversine_m(stops["stop_lat"], stops["stop_lon"], poi["lat"], poi["lon"]))
    near_stops = stops[stops["dist_m"] <= poi["stop_radius_m"]].sort_values("dist_m")
    ends = last.merge(stops[["stop_id", "dist_m"]], on="stop_id")
    ends_here = set(ends.loc[ends["dist_m"] <= poi["terminating_radius_m"], "trip_id"])
    at = stop_times[stop_times["stop_id"].isin(set(near_stops["stop_id"])) & ~stop_times["trip_id"].isin(ends_here)]
    first = at.assign(seq=at["stop_sequence"].astype(int)).sort_values("seq").groupby("trip_id").head(1)
    routes = routes.assign(route_name=routes["route_short_name"].fillna(routes["route_long_name"]))
    dep = (first.merge(trips[["trip_id", "route_id", "service_id", "trip_headsign"]], on="trip_id")
                .merge(routes[["route_id", "route_name", "route_long_name", "route_type"]], on="route_id")
                .merge(stops[["stop_id", "stop_name"]], on="stop_id"))
    dep["route_group"] = [classify_headsign(h, overrides, poi) for h in dep["trip_headsign"]]
    dep["capacity"] = dep["route_type"].astype(str).map(S.CAPACITY).fillna(1).astype(float)
    dep.insert(0, "poi", poi["key"])
    cols = ["poi", "trip_id", "service_id", "route_id", "route_name", "route_long_name", "route_type", "trip_headsign",
            "route_group", "capacity", "stop_id", "stop_name", "departure_time"]
    near = near_stops.assign(poi=poi["key"])[["poi", "stop_id", "stop_code", "stop_name", "dist_m"]]
    return dep[cols].reset_index(drop=True), near


def classify_headsign(headsign: str, overrides: dict, poi: dict) -> str:
    if headsign in overrides:
        return overrides[headsign]
    h = (headsign or "").lower()
    for kw, group in poi["route_group_rules"]:
        if kw in h:
            return group
    return poi["route_group_default"]


def active_services(date: str, calendar: pd.DataFrame, calendar_dates: pd.DataFrame) -> set:
    d = pd.Timestamp(date)
    ds, dn = d.strftime("%Y%m%d"), d.day_name().lower()
    base = calendar[(calendar[dn].astype(str) == "1") & (calendar["start_date"] <= ds) & (calendar["end_date"] >= ds)]
    s = set(base["service_id"])
    cd = calendar_dates[calendar_dates["date"] == ds]
    s |= set(cd.loc[cd["exception_type"].astype(str) == "1", "service_id"])
    s -= set(cd.loc[cd["exception_type"].astype(str) == "2", "service_id"])
    return s


def service_30min(departures: pd.DataFrame, calendar: pd.DataFrame, calendar_dates: pd.DataFrame, poi: dict) -> pd.DataFrame:
    """departures: one row per trip leaving the POI (service_id, departure_time 'HH:MM:SS', route_group, capacity).
    Scheduled departures and capacity (bus-equivalents) per service_day_type × slot_of_day × route_group
    (GTFS times ≥ 24:00 wrap)."""
    groups = S.groups(poi)
    hh = departures["departure_time"].str.split(":", expand=True).astype(int)
    d = departures.assign(slot_of_day=(hh[0] * 2 + hh[1] // 30) % S.SLOTS_PER_DAY)
    d = d[d["route_group"].isin(groups)]
    full = pd.MultiIndex.from_product([range(S.SLOTS_PER_DAY), groups], names=["slot_of_day", "route_group"])
    rows = []
    for sdt, date in S.SERVICE_REP_DATES.items():
        on = d[d["service_id"].isin(active_services(date, calendar, calendar_dates))]
        agg = on.groupby(["slot_of_day", "route_group"]).agg(scheduled_departures=("trip_id", "size"),
                                                             scheduled_capacity=("capacity", "sum"))
        rows.append(agg.reindex(full, fill_value=0).reset_index().assign(service_day_type=sdt, poi=poi["key"]))
    out = pd.concat(rows, ignore_index=True)
    return out[["poi", "service_day_type", "slot_of_day", "route_group", "scheduled_departures", "scheduled_capacity"]]


# ---------------------------------------------------------------- 07: transit pressure gap, readiness, actions
SEVERE = ("Strained", "Critical")
ICON = {"Prepared": "🟢", "Watch": "🟡", "Strained": "🟠", "Critical": "🔴"}
SERVICE_LABELS = {"WEEKDAY": "weekday", "SATURDAY": "Saturday", "SUNDAY_HOLIDAY": "Sunday/holiday"}


def build_timeline(slots: pd.DataFrame, slot_corridor: pd.DataFrame, forecast: pd.DataFrame,
                   service: pd.DataFrame, days: pd.DataFrame, poi: dict):
    """One row per slot × route group: forecast demand, scheduled capacity, gap and readiness for
    +30…+120 min, plus the action card text. Returns (timeline, typical_load per group × service day type)."""
    groups, gcorr = S.groups(poi), S.group_corridors(poi)
    slots = slots.sort_values("slot_ts").reset_index(drop=True)
    grid = pd.DatetimeIndex(slots["slot_ts"])
    n = len(grid)
    sod = slots["slot_of_day"].to_numpy()
    sdt = slots["service_day_type"].to_numpy()
    day_type = slots["day_type"].to_numpy()

    # scheduled capacity (bus-equivalents), smoothed over the slot and the next one (riders can take either)
    svc = service.pivot_table(index=["service_day_type", "slot_of_day"], columns="route_group",
                              values="scheduled_capacity", aggfunc="sum").reindex(columns=groups).fillna(0)
    smooth = {}
    for t, block in svc.groupby(level=0):
        b = block.droplevel(0).reindex(range(S.SLOTS_PER_DAY), fill_value=0)
        smooth[t] = sum(np.roll(b.to_numpy(), -k, axis=0) for k in range(S.SERVICE_SMOOTH_SLOTS)) / S.SERVICE_SMOOTH_SLOTS
    gi = {g: i for i, g in enumerate(groups)}

    fc = forecast[forecast["corridor"] != "ALL"]
    normal_days = set(pd.to_datetime(days.loc[days["surge_ratio"] < S.NORMAL_DAY_MAX_RATIO, "date"]))

    base_dep = slot_corridor.pivot_table(index="slot_ts", columns="corridor", values="baseline_departures").reindex(grid)

    per_h = {}
    for h in S.HORIZONS:
        f = fc[fc["horizon"] == h].pivot_table(index="target_slot_ts", columns="corridor", values=["dep_hat", "dep_actual"], aggfunc="sum")
        tgt_idx = np.arange(n) + h
        valid = tgt_idx < n
        tgt_idx = np.where(valid, tgt_idx, n - 1)
        tgt_ts = grid[tgt_idx]
        for g, cs in gcorr.items():
            dem = f["dep_hat"].reindex(index=tgt_ts, columns=cs).sum(axis=1, min_count=1).to_numpy()
            act = f["dep_actual"].reindex(index=tgt_ts, columns=cs).sum(axis=1, min_count=1).to_numpy()
            dem[~valid] = np.nan
            act[~valid] = np.nan
            s = np.array([smooth[sdt[i]][sod[i], gi[g]] for i in tgt_idx])
            usual = _floor(base_dep[cs].sum(axis=1).to_numpy(float), S.BASELINE_OCC_FLOOR_SHARE)[tgt_idx]
            per_h[(g, h)] = {"dem": dem, "act": act, "svc": s, "load": dem / np.maximum(s, 0.5),
                             "idx": dem / usual, "tgt": tgt_idx}

    # typical load per group × service day type (a normal Saturday is judged against Saturday service):
    # daytime target slots with service, all horizons pooled. Whole-year POIs: one value per service day
    # type from the training period. Seasonal POIs (baseline_weeks): the median daily daytime load of the
    # same service day type within ±baseline_weeks weeks, so a term day is judged against term.
    train = np.asarray(grid < pd.Timestamp(S.TRAIN_END))
    is_normal = np.asarray(grid.normalize().isin(list(normal_days)))
    lo, hi = S.TYPICAL_LOAD_SLOTS
    weeks = poi.get("baseline_weeks")
    dates = grid.normalize()
    sdt_of_date = pd.Series(sdt, index=dates).groupby(level=0).first()
    typical_load, typical_dem, tl_arr = {}, {}, {}
    for g in groups:
        loads, pos, dems = [], [], []
        for h in S.HORIZONS:
            d = per_h[(g, h)]
            tsod = sod[d["tgt"]]
            m = (tsod >= lo) & (tsod < hi) & (d["svc"] > 0) & np.isfinite(d["load"])
            m_train = m & train & is_normal
            dems.append(d["dem"][m_train])
            use = m if weeks else m_train
            loads.append(d["load"][use])
            pos.append(d["tgt"][use])
        load, pos = np.concatenate(loads), np.concatenate(pos)
        typical_dem[g] = float(np.median(np.concatenate(dems)))
        if not weeks:
            for t_sdt in S.SERVICE_REP_DATES:
                sel = sdt[pos] == t_sdt
                typical_load[(g, t_sdt)] = float(np.median(load[sel])) if sel.any() else np.nan
            tl_arr[g] = np.array([typical_load[(g, s)] for s in sdt])
        else:
            daily = pd.Series(load, index=dates[pos]).groupby(level=0).median()
            by_date = pd.Series(np.nan, index=sdt_of_date.index)
            for t_sdt in S.SERVICE_REP_DATES:
                ds = daily[sdt_of_date.reindex(daily.index).to_numpy() == t_sdt]
                if len(ds):
                    r = ds.rolling(f"{(2 * weeks + 1) * 7}D", center=True, min_periods=1).median()
                    by_date.loc[r.index] = r.to_numpy()
                    typical_load[(g, t_sdt)] = float(r.median())
                else:
                    typical_load[(g, t_sdt)] = np.nan
            tl_arr[g] = by_date.ffill().bfill().reindex(dates).to_numpy()

    frames = []
    for g in groups:
        t = pd.DataFrame({
            "poi": poi["key"], "slot_ts": grid, "date": grid.normalize(), "slot_of_day": sod, "day_type": day_type,
            "service_day_type": sdt, "route_group": g,
            "pressure": slots["pressure"].to_numpy(), "pressure_level": slots["pressure_level"].to_numpy(),
            "signature": slots["signature"].to_numpy(),
        })
        for h in S.HORIZONS:
            d = per_h[(g, h)]
            gap = d["load"] / tl_arr[g][d["tgt"]]
            no_service = d["svc"] <= 0
            gap[no_service] = np.nan
            quiet = d["dem"] < S.MIN_DEMAND_SHARE * typical_dem[g]
            t[f"dep_hat_h{h}"] = d["dem"]
            t[f"demand_idx_h{h}"] = d["idx"]
            t[f"actual_h{h}"] = d["act"]
            t[f"svc_h{h}"] = d["svc"]
            t[f"gap_h{h}"] = gap
            t[f"readiness_h{h}"] = [
                "No service" if ns else "Prepared" if q else level(x, S.READINESS_LEVELS)
                for x, ns, q in zip(gap, no_service, quiet)
            ]
        t["typical_load"] = tl_arr[g]
        t["typical_demand"] = typical_dem[g]
        frames.append(t)
    tl = pd.concat(frames, ignore_index=True)
    by_key = {g["key"]: g for g in poi["groups"]}
    actions = [_action(r, by_key[r.route_group]) for r in tl.itertuples(index=False)]
    tl["action_text"] = [a for a, _ in actions]
    tl["action_priority"] = pd.to_numeric(pd.Series([p for _, p in actions], dtype="float64"))
    return tl, typical_load


def _action(r, group: dict):
    levels = [getattr(r, f"readiness_h{h}") for h in S.HORIZONS]
    hits = [i for i, lv in enumerate(levels) if lv in SEVERE]
    if not hits:
        return None, None
    first = hits[0]
    last = first
    while last + 1 < len(levels) and levels[last + 1] in SEVERE:
        last += 1
    gaps = [getattr(r, f"gap_h{S.HORIZONS[i]}") for i in range(first, last + 1)]
    peak = max(gaps)
    worst = "Critical" if any(levels[i] == "Critical" for i in range(first, last + 1)) else "Strained"
    h0 = S.HORIZONS[first]
    win_start = r.slot_ts + h0 * SLOT
    win_end = r.slot_ts + (S.HORIZONS[last] + 1) * SLOT
    stage = win_start - pd.Timedelta(minutes=15)
    label = group["label"]
    day = SERVICE_LABELS[r.service_day_type]
    levers = "; ".join(lv.format(start=f"{stage:%H:%M}") for lv in group["levers"])
    text = (f"{ICON[worst]} {label} {worst.lower()} {win_start:%H:%M}–{win_end:%H:%M} (in {h0 * S.SLOT_MINUTES} min). "
            f"Expected {label.lower()} exit demand per unit of scheduled service is {peak:.1f}× a normal {day} "
            f"on the {day} schedule. {levers}.")
    return text, float(peak)


# ---------------------------------------------------------------- 08: outlook for future dates
def outlook_analogs(date: pd.Timestamp, days: pd.DataFrame, ref_day: pd.DataFrame) -> tuple[list, str]:
    """Past days that stand in for a future date, and a human-readable method:
    1. a holiday -> the same holiday last year; 2. else the same weekday 52 weeks earlier
    (+/- OUTLOOK_ANALOG_WEEKS); 3. else (no year-ago data) the typical same weekday in the training
    period. Holidays never stand in for ordinary days."""
    past = list(pd.to_datetime(days["date"]))
    labels = ref_day.assign(date=pd.to_datetime(ref_day["date"])).set_index("date")["label"]
    holidays = set(labels.index)
    lbl = labels.get(date)
    if lbl is not None:
        same = [d for d in past if labels.get(d) == lbl and d < date]
        if same:
            return [max(same)], f"{lbl}: based on {lbl} {max(same):%Y}"
    ordinary = set(past) - holidays
    near = [date - pd.Timedelta(weeks=52 + k) for k in range(-S.OUTLOOK_ANALOG_WEEKS, S.OUTLOOK_ANALOG_WEEKS + 1)]
    near = [d for d in near if d in ordinary]
    if len(near) == 1:
        return near, f"Based on the same day last year ({near[0]:%a %b %d, %Y})"
    if near:
        return near, f"Based on the same week last year ({min(near):%b %d}–{max(near):%b %d, %Y})"
    train = [d for d in ordinary if d.dayofweek == date.dayofweek and d < pd.Timestamp(S.TRAIN_END)]
    return train, f"Based on a typical {date:%A} (Nov 2025–Jun 2026)"


def service_for_date(departures: pd.DataFrame, calendar, calendar_dates, date, poi: dict) -> np.ndarray:
    """Scheduled capacity (bus-equivalents) per slot_of_day × route group on one actual calendar date."""
    groups = S.groups(poi)
    hh = departures["departure_time"].str.split(":", expand=True).astype(int)
    d = departures.assign(slot_of_day=(hh[0] * 2 + hh[1] // 30) % S.SLOTS_PER_DAY)
    d = d[d["route_group"].isin(groups) & d["service_id"].isin(active_services(date, calendar, calendar_dates))]
    m = d.pivot_table(index="slot_of_day", columns="route_group", values="capacity", aggfunc="sum")
    return m.reindex(index=range(S.SLOTS_PER_DAY), columns=groups).fillna(0).to_numpy()


def build_outlook(slots, slot_corridor, timeline, days, departures, calendar, calendar_dates, ref_day, poi: dict):
    """Expected conditions for future dates (OUTLOOK_START..OUTLOOK_END): analog past days (see
    outlook_analogs) for the crowd, and the actual published TransLink schedule of each date for service.
    Returns (outlook_days, outlook_slots, outlook_slot_corridor, outlook_timeline) shaped like the past tables."""
    groups, gcorr = S.groups(poi), S.group_corridors(poi)
    corridors = S.corridors(poi)
    dates = pd.date_range(S.OUTLOOK_START, S.OUTLOOK_END, freq="D")
    grid = pd.date_range(dates[0], dates[-1] + pd.Timedelta(days=1) - SLOT, freq=f"{S.SLOT_MINUTES}min")
    n = len(grid)
    sod = slot_of_day(grid)
    sdt_arr, _ = service_day_types(grid, ref_day)
    slots = slots.assign(date=pd.to_datetime(slots["date"]))
    sc = slot_corridor.assign(date=pd.to_datetime(slot_corridor["slot_ts"]).dt.normalize(),
                              slot_of_day=slot_of_day(slot_corridor["slot_ts"]))
    num_cols = ["arrivals", "departures", "occupancy", "baseline_occ", "pressure", "baseline_arrivals", "baseline_departures",
                "median_dwell", "local_share", "baseline_local_share", "regional_share", "baseline_regional_share",
                "trailing_dwell", "stay_ratio", "visitor_ratio"]
    corr_idx = pd.MultiIndex.from_product([corridors, range(S.SLOTS_PER_DAY)], names=["corridor", "slot_of_day"])
    past_ratio = days.assign(date=pd.to_datetime(days["date"])).set_index("date")["surge_ratio"]

    out_slots, out_corr, out_days = [], [], []
    for date in dates:
        analogs, method = outlook_analogs(date, days, ref_day)
        ts = pd.date_range(date, periods=S.SLOTS_PER_DAY, freq=f"{S.SLOT_MINUTES}min")
        a = slots[slots["date"].isin(analogs)].groupby("slot_of_day")[num_cols].median().reindex(range(S.SLOTS_PER_DAY))
        out_slots.append(a.reset_index().assign(poi=poi["key"], slot_ts=ts, date=date, day_type=S.DAY_TYPES[date.dayofweek]))
        c = (sc[sc["date"].isin(analogs)].groupby(["corridor", "slot_of_day"])[["departures", "baseline_departures", "share", "baseline_share"]]
             .median().reindex(corr_idx).reset_index())
        c["slot_ts"] = ts[c["slot_of_day"].to_numpy()]
        c["poi"] = poi["key"]
        out_corr.append(c)
        shown = sorted(analogs)
        out_days.append({"poi": poi["key"], "date": date, "day_type": S.DAY_TYPES[date.dayofweek], "method": method,
                         "analog_dates": ", ".join(f"{d:%Y-%m-%d}" for d in shown[:6]) + (" …" if len(shown) > 6 else ""),
                         "surge_ratio": float(past_ratio.reindex(shown).median())})
    o_slots = pd.concat(out_slots, ignore_index=True).sort_values("slot_ts").reset_index(drop=True)
    o_slots["service_day_type"] = sdt_arr
    o_slots["pressure_level"] = [level(p, S.PRESSURE_LEVELS) for p in o_slots["pressure"]]
    o_slots["signature"] = [signature(p, r, br, st, v) for p, r, br, st, v in zip(
        o_slots["pressure"], o_slots["regional_share"], o_slots["baseline_regional_share"], o_slots["stay_ratio"], o_slots["visitor_ratio"])]
    o_corr = pd.concat(out_corr, ignore_index=True)

    # days: expected surge ratio = median surge ratio of the analog days (each vs its own normal)
    o_days = pd.DataFrame(out_days)
    o_days["arrivals"] = o_slots.groupby("date")["arrivals"].sum().reindex(o_days["date"]).to_numpy()
    o_days["is_surge"] = o_days["surge_ratio"] >= poi.get("surge_threshold", S.SURGE_RATIO_THRESHOLD)
    o_days["service_day_type"], lbl = service_day_types(pd.DatetimeIndex(o_days["date"]), ref_day)
    o_days["label"] = [x if x else ("Expected surge" if s else "") for x, s in zip(lbl, o_days["is_surge"])]
    lo, hi = S.PEAK_WINDOW_SLOTS
    win = o_slots[(o_slots["slot_of_day"] >= lo) & (o_slots["slot_of_day"] < hi)]
    peak = win.loc[win.groupby("date")["pressure"].idxmax()]
    o_days["peak_pressure"] = peak["pressure"].to_numpy()
    o_days["peak_pressure_slot"] = peak["slot_ts"].to_numpy()

    # readiness: composite exits vs the actual published schedule of each date
    svc = np.concatenate([service_for_date(departures, calendar, calendar_dates, d, poi) for d in dates])
    smooth = sum(np.roll(svc, -k, axis=0) for k in range(S.SERVICE_SMOOTH_SLOTS)) / S.SERVICE_SMOOTH_SLOTS
    dep = o_corr.pivot_table(index="slot_ts", columns="corridor", values="departures").reindex(index=grid, columns=corridors)
    base = o_corr.pivot_table(index="slot_ts", columns="corridor", values="baseline_departures").reindex(index=grid, columns=corridors)
    typ_load = timeline.groupby(["route_group", "service_day_type"])["typical_load"].median()
    typ_dem = timeline.groupby("route_group")["typical_demand"].first()
    gi = {g: i for i, g in enumerate(groups)}

    frames = []
    for g in groups:
        cs = gcorr[g]
        dem_all = dep[cs].sum(axis=1, min_count=1).to_numpy()
        usual_all = _floor(base[cs].sum(axis=1).to_numpy(float), S.BASELINE_OCC_FLOOR_SHARE)
        t = pd.DataFrame({
            "poi": poi["key"], "slot_ts": grid, "date": grid.normalize(), "slot_of_day": sod,
            "day_type": np.array(S.DAY_TYPES)[grid.dayofweek], "service_day_type": sdt_arr, "route_group": g,
            "pressure": o_slots["pressure"].to_numpy(), "pressure_level": o_slots["pressure_level"].to_numpy(),
            "signature": o_slots["signature"].to_numpy(),
        })
        tl_arr = np.array([typ_load.get((g, s), np.nan) for s in sdt_arr])
        for h in S.HORIZONS:
            idx = np.arange(n) + h
            valid = idx < n
            tgt = np.minimum(idx, n - 1)
            dem = np.where(valid, dem_all[tgt], np.nan)
            s_ = smooth[tgt, gi[g]]
            gap = dem / np.maximum(s_, 0.5) / tl_arr[tgt]
            no_service = s_ <= 0
            gap[no_service] = np.nan
            quiet = dem < S.MIN_DEMAND_SHARE * typ_dem[g]
            t[f"dep_hat_h{h}"] = dem
            t[f"demand_idx_h{h}"] = dem / usual_all[tgt]
            t[f"actual_h{h}"] = np.nan
            t[f"svc_h{h}"] = s_
            t[f"gap_h{h}"] = gap
            t[f"readiness_h{h}"] = ["No service" if ns else "Prepared" if q else level(x, S.READINESS_LEVELS)
                                    for x, ns, q in zip(gap, no_service, quiet)]
        t["typical_load"] = tl_arr
        t["typical_demand"] = typ_dem[g]
        frames.append(t)
    o_tl = pd.concat(frames, ignore_index=True)
    by_key = {g["key"]: g for g in poi["groups"]}
    actions = [_action(r, by_key[r.route_group]) for r in o_tl.itertuples(index=False)]
    o_tl["action_text"] = [a for a, _ in actions]
    o_tl["action_priority"] = pd.to_numeric(pd.Series([p for _, p in actions], dtype="float64"))
    return o_days, o_slots, o_corr, o_tl
