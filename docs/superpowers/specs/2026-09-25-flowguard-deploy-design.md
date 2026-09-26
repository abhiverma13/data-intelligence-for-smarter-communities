# FlowGuard — Own Isolated Deployment in Workspace B

**Date:** 2026-09-25
**Status:** Approved design (pre-implementation)
**Owner:** Harsha (workspace user `aminharsh317@gmail.com`)

> Supersedes the earlier draft of this file, which targeted workspace A. That plan is no
> longer in effect.

## 1. Goal

Deploy an **own, isolated copy** of FlowGuard as a full live stack in workspace **B**, running
alongside (and never touching) the existing FlowGuard deployment owned by `a.verma1304@gmail.com`.

"Full live stack": raw data in a Unity Catalog Volume → pipeline notebooks 01–07 → gold tables →
a deployed Databricks App reading gold tables live (`DATA_MODE=live`) with the committed JSON
snapshot as fallback.

## 2. Target environment

| Item | Value |
|---|---|
| Workspace (B) | `dbc-4c89dd88-f18b.cloud.databricks.com` |
| User | `aminharsh317@gmail.com` (workspace admin, in `admins` group) |
| Edition | Databricks Free Edition (catalogs: `samples`, `system`, `workspace`) |
| SQL warehouse | `02696d2b2f33da73` ("Serverless Starter Warehouse", 2X-Small) — reuse; Free Edition has one |
| Existing FlowGuard | app `flowguard`, job `flowguard-refresh`, catalog `flowguard` (owner `a.verma1304`) — must stay untouched |
| Apps in use | 1 of 3 (`flowguard`); ours will be #2 |

## 3. Authentication (already done)

- **CLI:** profile `flowguard-b` written to `~/.databrickscfg` from B's PAT, verified with
  `current-user me`. Profile `flowguard-a` (workspace A) is retained.
- **MCP:** added `databricks-sql-b` and `databricks-functions-b` to
  `~/.config/opencode/opencode.json`, reading B's host/token from
  `~/.config/opencode/databricks/flowguard-b/{host,token}`. A's MCP entries are untouched.
  **Requires an opencode restart to load.**
- B's PAT: comment `Harsh`, expires **2026-10-09 20:25 PDT**. Never committed to the repo.

## 4. Namespaces and naming (collision-safe)

| Resource | Verma's (leave alone) | Ours |
|---|---|---|
| Catalog | `flowguard` | `workspace` |
| Schemas | `flowguard.bronze/silver/gold` | `workspace.flowguard_bronze / flowguard_silver / flowguard_gold` |
| Volume | `flowguard.bronze.raw` | `workspace.flowguard_bronze.raw` |
| App | `flowguard` | `flowguard-harsha` (own service principal) |
| Job | `flowguard-refresh` | `flowguard-refresh-harsha` |
| Code path | `/Workspace/Users/a.verma1304@gmail.com/...` | `/Workspace/Users/aminharsh317@gmail.com/data-intelligence-for-smarter-communities/flowguard` |
| Warehouse | `02696d2b2f33da73` | reuse the same warehouse |

The `workspace` catalog is owned by `_workspace_admins_workspace_7474644350406056`; our user is a
workspace admin and can read its permissions, so granting the app SP `USE CATALOG` on it is
expected to succeed. Fallback if it does not: create a dedicated catalog (`flowguard_harsha`),
which we would own, and grant on that instead.

## 5. Code changes (branch `deploy/harsha-workspace-b`)

| File | Change | Why |
|---|---|---|
| `pipeline/00_config.py` | Replace the `_catalog_exists` check with a `_catalog_usable` check (`USE CATALOG` in a try/except); fall back to `workspace` + `flowguard_` prefix when not usable | In B, catalog `flowguard` **exists but is not usable**; the current existence-only check would pick it and fail with permission-denied |
| `app/app.yaml` | `CATALOG: "workspace"`, `SCHEMA: "flowguard_gold"` | App cannot auto-resolve the fallback; must be pinned |
| `deploy/job.json` | Name → `flowguard-refresh-harsha`; notebook paths → our synced path | Avoid colliding with Verma's job |
| `pipeline/90_app_grants.py` | `APP_NAME = "flowguard-harsha"` | Grant the correct app SP |
| `deploy/app.json` | **No change** — warehouse id `02696d2b2f33da73` already matches B | — |

No changes to formulas (`fg_core.py`), thresholds (`fg_settings.py`), or any metric logic.

## 6. Data

| File | Source | Destination |
|---|---|---|
| `synthetic_park_royal_mall.csv` (~387 MB) | `~/Desktop/Projects/Databricks-hackathon/` | `workspace.flowguard_bronze.raw/` root |
| TransLink GTFS `*.txt` | `https://gtfs-static.translink.ca/gtfs/google_transit.zip`, downloaded on the laptop (B's notebooks have no outbound internet) | `workspace.flowguard_bronze.raw/gtfs/` |

## 7. Execution phases

1. **Branch + code edits** — apply section 5; keep `main` clean.
2. **Sync code to B** — `databricks sync ./flowguard /Workspace/Users/aminharsh317@gmail.com/data-intelligence-for-smarter-communities/flowguard --profile flowguard-b`.
3. **UC setup** — run `01_setup_uc`; confirm it resolved to `workspace` + `flowguard_*` schemas and created the `raw` Volume.
4. **Upload data** — `databricks fs cp` the CSV and GTFS into `workspace.flowguard_bronze.raw`.
5. **Pipeline** — create + run `flowguard-refresh-harsha` (01→02→03→04→05, 02→06, 05+06→07); verify gold tables.
6. **App** — `databricks apps create flowguard-harsha` with the `sql-warehouse` resource → run `90_app_grants` → sync `app/` → `databricks apps deploy`.
7. **Verify** — `/api/health` shows `last_source=live`; run the `07_timeline` checkpoints.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| `00_config` picks Verma's `flowguard` catalog | `_catalog_usable` change in section 5 |
| Cannot grant `USE CATALOG` on `workspace` | Fall back to a dedicated `flowguard_harsha` catalog we own |
| Serverless job spec rejected | Fall back to one-off notebook runs or a `jobs submit` serverless environment |
| Free Edition compute quota | Pipeline runs once over ~5.4M rows; the app snapshot fallback covers outages |
| App quota (max 3) | 1 in use; ours is #2 |
| GTFS URL unreachable | 01–05 still deploy and the app runs; only readiness (06/07) is missing |
| PAT expiry 2026-10-09 | Deployment completes well before; renew or switch to OAuth after |

## 9. Verification / definition of done

- `01_setup_uc` resolves to `workspace.flowguard_bronze/silver/gold` (not `flowguard.*`).
- Gold tables populated: `pr_slots`, `egress_forecast`, `model_backtest`, `gtfs_service_30min`, `flowguard_timeline`.
- App `flowguard-harsha` ACTIVE; `/api/health` reports `last_source=live`.
- `07_timeline` checkpoints: Boxing Day `2025-12-26` shows eastbound/downtown Strained/Critical around midday and a first action card near 11:00; normal Saturday `2026-04-25` stays mostly Prepared.
- Backtest R² in the expected range (egress ~0.94–0.97 vs typical-week ~0.64).
- Verma's `flowguard` app, job, and catalog are byte-for-byte unchanged.
- No absolute headcounts in the UI; snapshot fallback works with the warehouse stopped.

## 10. Out of scope

- Touching Verma's app, job, catalog, or workspace files.
- Creating a new Databricks account or workspace.
- Genie space, after-hours watch, and other stretch features.
- Refreshing the committed snapshot from live data (optional; requires GTFS locally).
