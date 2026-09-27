# FlowGuard

*See the pressure before it hits.* Built for the Data Intelligence for Smarter Communities hackathon
(Rogers × Databricks × UBC, September 2026).

FlowGuard is an operations console for TransLink staff. It uses anonymous cell-tower data (how many people are
at a place and how long they stay) to forecast when crowds will leave, up to two hours ahead, and whether
scheduled transit service is ready for them. It covers three locations: **Park Royal**, **UBC** and
**Waterfront Station**.

- **Exit forecast:** R² 0.93–0.99 from 30 minutes to 2 hours ahead on unseen months, against 0.64 for a
  typical-week average at Park Royal.
- **Transit readiness and actions:** expected exit demand per unit of scheduled service (TransLink GTFS) for each
  group of routes, with operator actions and lead time.
- **After-hours watch:** flags unusual overnight activity, based on volume only.
- **Outlook:** expected conditions for future dates (Sept 2026 – Jan 2027) against the published timetable.
- **Scenario Lab, printable operator briefing, and Ask FlowGuard** (Databricks Genie chat).

Built end to end on Databricks: Unity Catalog (bronze → silver → gold), an 8-step serverless pipeline Job,
MLflow, Genie and a Databricks App.

Everything lives in [flowguard/](flowguard/). Start with [flowguard/README.md](flowguard/README.md) for the
layout, how to run the pipeline and app, and how to deploy. Verified numbers are in
[flowguard/docs/data_findings.md](flowguard/docs/data_findings.md).
