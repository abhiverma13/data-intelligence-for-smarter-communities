# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 90 — Grant the app read access
# MAGIC The Databricks App runs as its own **service principal**. It needs `USE CATALOG`, `USE SCHEMA` and `SELECT`
# MAGIC on the gold schema (adapted from workshop Lab 06). `CAN_USE` on the SQL warehouse comes from the app's
# MAGIC `sql-warehouse` resource; if a Genie space is configured, `CAN_RUN` on it is granted too.
# MAGIC
# MAGIC Run once after the app `flowguard` exists (re-running is harmless).

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

from databricks.sdk import WorkspaceClient

APP_NAME = "flowguard"
GENIE_SPACE_ID = ""   # stretch: paste the Genie space id here, then re-run

w = WorkspaceClient()
app = w.apps.get(name=APP_NAME)
sp = app.service_principal_client_id
print(f"App {APP_NAME} runs as service principal {app.service_principal_name} ({sp})")

for stmt in [
    f"GRANT USE CATALOG ON CATALOG `{CATALOG}` TO `{sp}`",
    f"GRANT USE SCHEMA ON SCHEMA {GOLD} TO `{sp}`",
    f"GRANT SELECT ON SCHEMA {GOLD} TO `{sp}`",
]:
    spark.sql(stmt)
    print("✅", stmt)

if GENIE_SPACE_ID:
    w.api_client.do("PATCH", f"/api/2.0/permissions/genie/{GENIE_SPACE_ID}",
                    body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_RUN"}]})
    print(f"✅ CAN_RUN on Genie space {GENIE_SPACE_ID}")
