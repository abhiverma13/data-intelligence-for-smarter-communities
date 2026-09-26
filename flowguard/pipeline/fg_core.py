"""FlowGuard analytics core: every formula from spec §5.2, in plain pandas/numpy.

Imported by the Databricks notebooks (04–07) *and* by local scripts, so both compute
identical numbers. Spark only does the heavy lifting (aggregating ~5.4M visits to slots);
everything here works on small slot-level frames (≈15k slots).

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
def build_slots(slot_agg: pd.DataFrame, corr_agg: pd.DataFrame, ref_day: pd.DataFrame):
    """
    slot_agg: index slot_ts (full grid) · arrivals, departures, arrivals_occ, departures_occ, median_dwell
    corr_agg: long frame slot_ts, corridor, arrivals, departures (full grid × CORRIDORS)
    Returns (pr_slots, pr_slot_corridor, pr_days).
    """
    grid = make_grid()
    a = slot_agg.reindex(grid).fillna({"arrivals": 0, "departures": 0, "arrivals_occ": 0, "departures_occ": 0})
    dow = np.asarray(grid.dayofweek)
    sod = slot_of_day(grid)
    out = pd.DataFrame(index=grid)
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
    base_occ = _typical(out["occupancy"].to_numpy(float), dow, sod)
    out["baseline_occ"] = base_occ
    out["pressure"] = out["occupancy"] / _floor(base_occ, S.BASELINE_OCC_FLOOR_SHARE)
    out["pressure_level"] = [level(p, S.PRESSURE_LEVELS) for p in out["pressure"]]
    out["baseline_arrivals"] = _typical(out["arrivals"].to_numpy(float), dow, sod)
    out["baseline_departures"] = _typical(out["departures"].to_numpy(float), dow, sod)
    out["median_dwell"] = a["median_dwell"].to_numpy(float)

    # corridor frame
    ca = corr_agg.pivot_table(index="slot_ts", columns="corridor", values="arrivals", aggfunc="sum")
    ca = ca.reindex(index=grid, columns=S.CORRIDORS).fillna(0)
    cd = corr_agg.pivot_table(index="slot_ts", columns="corridor", values="departures", aggfunc="sum")
    cd = cd.reindex(index=grid, columns=S.CORRIDORS).fillna(0)
    corr_rows = []
    for c in S.CORRIDORS:
        dep_c = cd[c].to_numpy(float)
        corr_rows.append(pd.DataFrame({
            "slot_ts": grid, "corridor": c,
            "arrivals": ca[c].astype(int).to_numpy(), "departures": dep_c.astype(int),
            "baseline_departures": _typical(dep_c, dow, sod),
        }))
    pr_slot_corridor = pd.concat(corr_rows, ignore_index=True)

    # 8. mobility signature on the trailing 2 h of arrivals
    trail = ca.rolling(S.TRAILING_SLOTS, min_periods=1).sum()
    tot = trail.sum(axis=1).replace(0, np.nan)
    shares = trail.div(tot, axis=0)
    for c in S.CORRIDORS:
        out[f"share_{c.lower()}"] = shares[c].to_numpy()
        out[f"baseline_share_{c.lower()}"] = _typical(shares[c].to_numpy(float), dow, sod)
    regional = shares[S.REGIONAL_CORRIDORS].sum(axis=1).to_numpy()
    out["regional_share"] = regional
    out["baseline_regional_share"] = _typical(regional, dow, sod)
    w = a["arrivals"].to_numpy(float)
    md = np.nan_to_num(out["median_dwell"].to_numpy(float))
    num = pd.Series(md * w).rolling(S.TRAILING_SLOTS, min_periods=1).sum().to_numpy()
    den = pd.Series(w).rolling(S.TRAILING_SLOTS, min_periods=1).sum().replace(0, np.nan).to_numpy()
    trailing_dwell = num / den
    out["trailing_dwell"] = trailing_dwell
    out["stay_ratio"] = trailing_dwell / _typical(trailing_dwell, dow, sod)
    visitor = shares["OUT_OF_REGION"].to_numpy()
    out["visitor_ratio"] = visitor / _typical(visitor, dow, sod)
    out["signature"] = [
        signature(p, r, br, s, v)
        for p, r, br, s, v in zip(out["pressure"], regional, out["baseline_regional_share"], out["stay_ratio"], out["visitor_ratio"])
    ]

    # 3. days: surge ratio vs median for that weekday
    days = out.groupby("date").agg(arrivals=("arrivals", "sum")).reset_index()
    days["day_type"] = np.array(S.DAY_TYPES)[days["date"].dt.dayofweek]
    days["surge_ratio"] = days["arrivals"] / days.groupby("day_type")["arrivals"].transform("median")
    days["is_surge"] = days["surge_ratio"] >= S.SURGE_RATIO_THRESHOLD
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

    return out.reset_index(drop=True), pr_slot_corridor, days


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
def estimate_kernel(k_counts: pd.Series) -> pd.DataFrame:
    """k_counts: number of training visits per egress_k (slots between arrival and departure slot).
    P(k) = share of visitors leaving k slots after their arrival slot; mass beyond K never returns."""
    total = k_counts.sum()
    k = np.arange(S.KERNEL_MAX_K + 1)
    p = k_counts.reindex(k, fill_value=0).to_numpy(float) / total
    return pd.DataFrame({"k": k, "p": p, "cum_p": np.cumsum(p)})


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


def run_forecasts(pr_slots: pd.DataFrame, pr_slot_corridor: pd.DataFrame, kernel: pd.DataFrame):
    """Returns (egress_forecast long frame, model_backtest frame) for corridor 'ALL' + each corridor."""
    p = kernel.sort_values("k")["p"].to_numpy()
    pr_slots = pr_slots.sort_values("slot_ts").reset_index(drop=True)
    grid = pd.DatetimeIndex(pr_slots["slot_ts"])
    dow = np.asarray(grid.dayofweek)
    sod = pr_slots["slot_of_day"].to_numpy()
    train = np.asarray(grid < pd.Timestamp(S.TRAIN_END))
    n = len(grid)

    series = {"ALL": (pr_slots["arrivals"].to_numpy(float), pr_slots["departures"].to_numpy(float),
                      pr_slots["baseline_departures"].to_numpy(float))}
    for c, g in pr_slot_corridor.groupby("corridor"):
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
                "origin_slot_ts": grid[tgt - h], "horizon": h, "target_slot_ts": grid[tgt],
                "corridor": c, "dep_hat": pred[tgt], "dep_actual": dep[tgt].astype(int),
                "egress_idx": pred[tgt] / base_dep_f[tgt],
            }))
            if c == "ALL":
                m = ~train & ~np.isnan(pred)
                m[:h] = False
                backtest.append(_metrics(h, "egress", "departures", dep[m], pred[m]))
                static = egress_forecast(arr, typ_arr, p, h, scaling=False)
                backtest.append(_metrics(h, "egress_static", "departures", dep[m], static[m]))
                typ_dep = _typical(dep, dow, sod, train)
                backtest.append(_metrics(h, "typical_week", "departures", dep[m], typ_dep[m]))
    fc = pd.concat(rows, ignore_index=True).dropna(subset=["dep_hat"])
    return fc, pd.DataFrame(backtest)


def _metrics(h, model, target, y, yhat) -> dict:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return {"horizon": h, "model": model, "target": target, "r2": 1 - ss_res / ss_tot,
            "mae": float(np.mean(np.abs(y - yhat))), "n": int(len(y))}


def gbt_arrival_backtest(pr_slots: pd.DataFrame) -> pd.DataFrame:
    """Gradient-boosted arrival forecaster (lags + calendar) vs the typical-week pattern.
    Included for comparison with the egress model; arrivals are not a product feature."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    pr_slots = pr_slots.sort_values("slot_ts").reset_index(drop=True)
    grid = pd.DatetimeIndex(pr_slots["slot_ts"])
    arr = pr_slots["arrivals"].astype(float).reset_index(drop=True)
    train = pd.Series(grid < pd.Timestamp(S.TRAIN_END))
    X = pd.DataFrame({"dow": grid.dayofweek, "sod": pr_slots["slot_of_day"].to_numpy()})
    for lag in [0, 1, 2, 3, 47, 335]:          # lag 0 = arrivals in the current slot (known at t)
        X[f"lag{lag}"] = arr.shift(lag)
    dow, sod = np.asarray(grid.dayofweek), pr_slots["slot_of_day"].to_numpy()
    typ = _typical(arr.to_numpy(), dow, sod, train.to_numpy())
    rows = []
    for h in [1, 2]:
        y = arr.shift(-h)
        ok = X.notna().all(axis=1) & y.notna()
        model = HistGradientBoostingRegressor(max_iter=300, random_state=0).fit(X[ok & train], y[ok & train])
        te = ok & ~train
        rows.append(_metrics(h, "gbt_arrivals", "arrivals", y[te], model.predict(X[te])))
        rows.append(_metrics(h, "typical_week", "arrivals", y[te], np.roll(typ, -h)[te.to_numpy()]))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 06: GTFS route groups + scheduled service
def haversine_m(lat, lon, lat0=S.POI_LAT, lon0=S.POI_LON):
    lat, lon = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    lat0, lon0 = np.radians(lat0), np.radians(lon0)
    a = np.sin((lat - lat0) / 2) ** 2 + np.cos(lat0) * np.cos(lat) * np.sin((lon - lon0) / 2) ** 2
    return 2 * 6371000 * np.arcsin(np.sqrt(a))


def parkroyal_departures(stops, stop_times, trips, routes, overrides: dict):
    """One row per scheduled trip that *leaves* Park Royal: its first stop within GTFS_STOP_RADIUS_M,
    excluding trips that end near the mall (those arrive rather than depart). Returns (departures, near_stops)."""
    stops = stops.assign(dist_m=haversine_m(stops["stop_lat"], stops["stop_lon"]))
    near_stops = stops[stops["dist_m"] <= S.GTFS_STOP_RADIUS_M].sort_values("dist_m")
    st = stop_times.assign(seq=stop_times["stop_sequence"].astype(int))
    last = st.loc[st.groupby("trip_id")["seq"].idxmax(), ["trip_id", "stop_id", "seq"]]
    last = last.merge(stops[["stop_id", "dist_m"]], on="stop_id")
    ends_here = set(last.loc[last["dist_m"] <= S.GTFS_TERMINATING_RADIUS_M, "trip_id"])
    at_pr = st[st["stop_id"].isin(set(near_stops["stop_id"])) & ~st["trip_id"].isin(ends_here)]
    first = at_pr.sort_values("seq").groupby("trip_id").head(1)
    dep = (first.merge(trips[["trip_id", "route_id", "service_id", "trip_headsign"]], on="trip_id")
                .merge(routes[["route_id", "route_short_name", "route_long_name"]], on="route_id")
                .merge(stops[["stop_id", "stop_name"]], on="stop_id"))
    dep["route_group"] = [classify_headsign(h, overrides) for h in dep["trip_headsign"]]
    cols = ["trip_id", "service_id", "route_id", "route_short_name", "route_long_name", "trip_headsign",
            "route_group", "stop_id", "stop_name", "departure_time"]
    return dep[cols].reset_index(drop=True), near_stops[["stop_id", "stop_code", "stop_name", "dist_m"]]


def classify_headsign(headsign: str, overrides: dict) -> str:
    if headsign in overrides:
        return overrides[headsign]
    h = (headsign or "").lower()
    for kw, group in S.ROUTE_GROUP_RULES:
        if kw in h:
            return group
    return S.ROUTE_GROUP_DEFAULT


def active_services(date: str, calendar: pd.DataFrame, calendar_dates: pd.DataFrame) -> set:
    d = pd.Timestamp(date)
    ds, dn = d.strftime("%Y%m%d"), d.day_name().lower()
    base = calendar[(calendar[dn].astype(str) == "1") & (calendar["start_date"] <= ds) & (calendar["end_date"] >= ds)]
    s = set(base["service_id"])
    cd = calendar_dates[calendar_dates["date"] == ds]
    s |= set(cd.loc[cd["exception_type"].astype(str) == "1", "service_id"])
    s -= set(cd.loc[cd["exception_type"].astype(str) == "2", "service_id"])
    return s


def service_30min(departures: pd.DataFrame, calendar: pd.DataFrame, calendar_dates: pd.DataFrame) -> pd.DataFrame:
    """departures: one row per trip leaving Park Royal (service_id, departure_time 'HH:MM:SS', route_group).
    Counts scheduled departures per service_day_type × slot_of_day × route_group (times ≥ 24:00 wrap)."""
    hh = departures["departure_time"].str.split(":", expand=True).astype(int)
    d = departures.assign(slot_of_day=(hh[0] * 2 + hh[1] // 30) % S.SLOTS_PER_DAY)
    d = d[d["route_group"].isin(S.ROUTE_GROUPS)]
    rows = []
    for sdt, date in S.SERVICE_REP_DATES.items():
        on = d[d["service_id"].isin(active_services(date, calendar, calendar_dates))]
        counts = on.groupby(["slot_of_day", "route_group"]).size()
        full = pd.MultiIndex.from_product([range(S.SLOTS_PER_DAY), S.ROUTE_GROUPS], names=["slot_of_day", "route_group"])
        rows.append(counts.reindex(full, fill_value=0).rename("scheduled_departures").reset_index().assign(service_day_type=sdt))
    out = pd.concat(rows, ignore_index=True)
    return out[["service_day_type", "slot_of_day", "route_group", "scheduled_departures"]]


# ---------------------------------------------------------------- 07: transit pressure gap, readiness, actions
LEVERS = {
    "EASTBOUND": ("Stage supplemental eastbound (R2-direction) trips at Park Royal exchange from {start}; "
                  "position passenger-management staff at the eastbound bays; message riders to consider a later departure."),
    "DOWNTOWN": ("Add short-turn downtown trips (250/257 direction) from {start}; flag southbound Lions Gate "
                 "pressure to bridge operations; staff the downtown bays."),
    "WEST_VAN_LOCAL": ("Ask West Vancouver Blue Bus to hold a spare local bus at Park Royal from {start}; "
                       "open overflow wayfinding at the local bays."),
}
SEVERE = ("Strained", "Critical")
ICON = {"Prepared": "🟢", "Watch": "🟡", "Strained": "🟠", "Critical": "🔴"}


def build_timeline(pr_slots: pd.DataFrame, pr_slot_corridor: pd.DataFrame, forecast: pd.DataFrame,
                   service: pd.DataFrame, pr_days: pd.DataFrame):
    """One row per slot × route group: forecast demand, scheduled trips, gap and readiness for
    +30…+120 min, plus the action card text. Returns (timeline, typical_load per group)."""
    pr_slots = pr_slots.sort_values("slot_ts").reset_index(drop=True)
    grid = pd.DatetimeIndex(pr_slots["slot_ts"])
    n = len(grid)
    sod = pr_slots["slot_of_day"].to_numpy()
    sdt = pr_slots["service_day_type"].to_numpy()
    day_type = pr_slots["day_type"].to_numpy()

    # scheduled trips, smoothed over the slot and the next one (riders can take either bus)
    svc = service.pivot_table(index=["service_day_type", "slot_of_day"], columns="route_group",
                              values="scheduled_departures", aggfunc="sum").reindex(columns=S.ROUTE_GROUPS).fillna(0)
    smooth = {}
    for t, block in svc.groupby(level=0):
        b = block.droplevel(0).reindex(range(S.SLOTS_PER_DAY), fill_value=0)
        smooth[t] = sum(np.roll(b.to_numpy(), -k, axis=0) for k in range(S.SERVICE_SMOOTH_SLOTS)) / S.SERVICE_SMOOTH_SLOTS
    gi = {g: i for i, g in enumerate(S.ROUTE_GROUPS)}

    fc = forecast[forecast["corridor"] != "ALL"]
    normal_days = set(pd.to_datetime(pr_days.loc[pr_days["surge_ratio"] < S.NORMAL_DAY_MAX_RATIO, "date"]))

    base_dep = pr_slot_corridor.pivot_table(index="slot_ts", columns="corridor", values="baseline_departures").reindex(grid)

    per_h = {}
    for h in S.HORIZONS:
        f = fc[fc["horizon"] == h].pivot_table(index="target_slot_ts", columns="corridor", values=["dep_hat", "dep_actual"], aggfunc="sum")
        tgt_idx = np.arange(n) + h
        valid = tgt_idx < n
        tgt_idx = np.where(valid, tgt_idx, n - 1)
        tgt_ts = grid[tgt_idx]
        for g, cs in S.GROUP_CORRIDORS.items():
            dem = f["dep_hat"].reindex(index=tgt_ts, columns=cs).sum(axis=1, min_count=1).to_numpy()
            act = f["dep_actual"].reindex(index=tgt_ts, columns=cs).sum(axis=1, min_count=1).to_numpy()
            dem[~valid] = np.nan
            act[~valid] = np.nan
            s = np.array([smooth[sdt[i]][sod[i], gi[g]] for i in tgt_idx])
            usual = _floor(base_dep[cs].sum(axis=1).to_numpy(float), S.BASELINE_OCC_FLOOR_SHARE)[tgt_idx]
            per_h[(g, h)] = {"dem": dem, "act": act, "svc": s, "load": dem / np.maximum(s, 0.5),
                             "idx": dem / usual, "tgt": tgt_idx}

    # typical load per group × service day type (a normal Saturday is judged against Saturday service):
    # normal days, training period, daytime target slots with service, all horizons pooled
    train = np.asarray(grid < pd.Timestamp(S.TRAIN_END))
    is_normal = np.asarray(grid.normalize().isin(list(normal_days)))
    lo, hi = S.TYPICAL_LOAD_SLOTS
    typical_load, typical_dem = {}, {}
    for g in S.ROUTE_GROUPS:
        dems = []
        for t_sdt in S.SERVICE_REP_DATES:
            vals = []
            for h in S.HORIZONS:
                d = per_h[(g, h)]
                tsod = sod[d["tgt"]]
                m = (train & is_normal & (sdt[d["tgt"]] == t_sdt) & (tsod >= lo) & (tsod < hi)
                     & (d["svc"] > 0) & np.isfinite(d["load"]))
                vals.append(d["load"][m])
                dems.append(d["dem"][m])
            vals = np.concatenate(vals)
            typical_load[(g, t_sdt)] = float(np.median(vals)) if len(vals) else np.nan
        typical_dem[g] = float(np.median(np.concatenate(dems)))

    frames = []
    for g in S.ROUTE_GROUPS:
        t = pd.DataFrame({
            "slot_ts": grid, "date": grid.normalize(), "slot_of_day": sod, "day_type": day_type,
            "service_day_type": sdt, "route_group": g,
            "pressure": pr_slots["pressure"].to_numpy(), "pressure_level": pr_slots["pressure_level"].to_numpy(),
            "signature": pr_slots["signature"].to_numpy(),
        })
        for h in S.HORIZONS:
            d = per_h[(g, h)]
            tl_ = np.array([typical_load[(g, s)] for s in sdt[d["tgt"]]])
            gap = d["load"] / tl_
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
        t["typical_load"] = [typical_load[(g, s)] for s in sdt]
        frames.append(t)
    tl = pd.concat(frames, ignore_index=True)
    actions = [_action(r) for r in tl.itertuples(index=False)]
    tl["action_text"] = [a for a, _ in actions]
    tl["action_priority"] = pd.to_numeric(pd.Series([p for _, p in actions], dtype="float64"))
    return tl, typical_load


def _action(r):
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
    label = S.GROUP_LABELS[r.route_group]
    day = {"WEEKDAY": "weekday", "SATURDAY": "Saturday", "SUNDAY_HOLIDAY": "Sunday/holiday"}[r.service_day_type]
    text = (f"{ICON[worst]} {label} {worst.lower()} {win_start:%H:%M}–{win_end:%H:%M} (in {h0 * S.SLOT_MINUTES} min). "
            f"Expected {label.lower()} exit demand per scheduled trip is {peak:.1f}× a normal {day} on the {day} schedule. "
            + LEVERS[r.route_group].format(start=f"{stage:%H:%M}"))
    return text, float(peak)
