# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 05 — Egress model (the ML component)
# MAGIC Forecasts **departures** 30–120 min ahead from arrivals already inside plus a learned dwell kernel
# MAGIC (spec §5.2.4), separately for every point of interest:
# MAGIC
# MAGIC - **Kernel** `P(k)` = share of visitors who leave `k` half-hour slots after their arrival slot, learned on data before `TRAIN_END`.
# MAGIC - **Forecast** made at slot `t` for `t+h`: `Σ arrivals already in × P  +  Σ typical arrivals still to come × today's busyness × P`.
# MAGIC   "Today's busyness" = trailing-2 h arrivals ÷ typical (set `TODAY_SCALING = False` in `fg_settings` for the plain spec formula).
# MAGIC - **Backtest** on the Jul–Aug holdout vs the typical-week pattern, the unscaled egress model, and a gradient-boosted
# MAGIC   arrival forecaster. Each POI is logged as its own **MLflow** run.
# MAGIC
# MAGIC Writes `gold.egress_kernel`, `gold.egress_forecast`, `gold.model_backtest`.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Learn one egress kernel per POI on the training period
from pyspark.sql import functions as F

k_all = (
    spark.table(f"{SILVER}.visits")
    .filter(F.col("arrival_slot") < F.lit(TRAIN_END).cast("timestamp_ntz"))
    .groupBy("poi", "egress_k").count().toPandas()
)
kernels = {key: fg_core.estimate_kernel(g.set_index("egress_k")["count"], POI_BY_KEY[key]) for key, g in k_all.groupby("poi")}
for key, kernel in kernels.items():
    print(f"{POI_BY_KEY[key]['name']:11s} share gone after k half-hour slots:",
          {int(k): round(c, 3) for k, c in zip(kernel["k"], kernel["cum_p"]) if k in (0, 1, 2, 4, 8, 16)})

# COMMAND ----------

# DBTITLE 1,Forecast every slot for +30…+120 min and backtest on the holdout
slots_all = from_spark(spark.table(f"{GOLD}.slots"), ts_cols=["slot_ts"], date_cols=["date"])
sc_all = from_spark(spark.table(f"{GOLD}.slot_corridor"), ts_cols=["slot_ts"])

forecasts, backtests = [], []
for key, kernel in kernels.items():
    poi = POI_BY_KEY[key]
    slots = slots_all[slots_all["poi"] == key]
    fc, bt = fg_core.run_forecasts(slots, sc_all[sc_all["poi"] == key], kernel, poi)
    try:
        bt = pd.concat([bt, fg_core.gbt_arrival_backtest(slots, poi)], ignore_index=True)
    except ImportError:
        print("⚠️ scikit-learn not available — skipping the GBT arrival comparison.")
    forecasts.append(fc); backtests.append(bt)
    print(f"== {poi['name']} holdout R²")
    print(bt.pivot_table(index=["target", "model"], columns="horizon", values="r2").round(3).to_string())
forecast = pd.concat(forecasts, ignore_index=True)
backtest = pd.concat(backtests, ignore_index=True)
kernel_all = pd.concat(kernels.values(), ignore_index=True)

# COMMAND ----------

# DBTITLE 1,Write gold tables
write_gold(kernel_all, "egress_kernel",
           f"Egress kernel per point of interest, learned on visits arriving before {TRAIN_END}: share of visitors leaving k half-hour slots after their arrival slot.",
           {"k": "Half-hour slots between arrival slot and departure slot",
            "p": "Share of visitors whose departure is exactly k slots after arrival",
            "cum_p": "Share of visitors gone within k slots"})
write_gold(forecast, "egress_forecast",
           "Departure forecasts made at each slot for +30 to +120 min, per point of interest, overall (ALL) and per corridor, with the actual departures for comparison.",
           {"origin_slot_ts": "Slot at which the forecast is made (only data up to this slot is used)",
            "horizon": "Forecast horizon in 30-minute steps (1 = +30 min … 4 = +120 min)",
            "target_slot_ts": "Slot being forecast",
            "corridor": "ALL or an outbound corridor",
            "dep_hat": "Forecast departures (sample scale)",
            "dep_actual": "Actual departures in the target slot",
            "egress_idx": "dep_hat divided by the usual departures for this corridor, weekday and slot (x normal)"},
           ts_cols=["origin_slot_ts", "target_slot_ts"])
write_gold(backtest, "model_backtest",
           f"Holdout backtest per point of interest (target slots from {TRAIN_END}): R2 and MAE per horizon and model.",
           {"model": "egress (scaled by today busyness), egress_static (spec formula), typical_week (median for weekday x slot), gbt_arrivals (gradient-boosted arrival forecaster)",
            "target": "departures or arrivals"})

# COMMAND ----------

# DBTITLE 1,Log one MLflow run per POI
import mlflow

me = spark.sql("SELECT current_user()").first()[0]
try:
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
except Exception as e:
    print(f"⚠️ Could not use {MLFLOW_EXPERIMENT} ({type(e).__name__}); using your home folder instead.")
    mlflow.set_experiment(f"/Users/{me}/flowguard-egress")

for key, kernel in kernels.items():
    bt = backtest[backtest["poi"] == key]
    with mlflow.start_run(run_name=f"egress-kernel-{key}") as run:
        mlflow.set_tag("poi", key)
        mlflow.log_params({"poi": key, "train_end": TRAIN_END, "kernel_max_k": KERNEL_MAX_K, "slot_minutes": SLOT_MINUTES,
                           "today_scaling": TODAY_SCALING, "today_factor_clip": str(TODAY_FACTOR_CLIP),
                           "occ_exclude_long_stay": OCC_EXCLUDE_LONG_STAY,
                           "baseline_weeks": POI_BY_KEY[key].get("baseline_weeks") or "all"})
        for r in bt.itertuples(index=False):
            mlflow.log_metric(f"r2_{r.target}_{r.model}_h{r.horizon}", float(r.r2))
            mlflow.log_metric(f"mae_{r.target}_{r.model}_h{r.horizon}", float(r.mae))
        mlflow.log_text(kernel.to_csv(index=False), "egress_kernel.csv")
        mlflow.log_text(bt.to_csv(index=False), "model_backtest.csv")
        print(f"✅ MLflow run {run.info.run_id} ({key})")
