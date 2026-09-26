# FlowGuard — Park Royal Mobility Intelligence

*See the pressure before it hits.* Built for the Data Intelligence for Smarter Communities
hackathon (Rogers × Databricks × UBC, Sept 2026).

FlowGuard uses dwell time in Park Royal's synthetic cell-tower data to forecast **when** a
crowd will leave, **which direction** it heads, and whether scheduled TransLink service is
ready for it — giving transit operators lead time instead of reaction.

Everything lives in [flowguard/](flowguard/): see [flowguard/README.md](flowguard/README.md) for
layout and run order, and [flowguard/docs/FLOWGUARD_SPEC.md](flowguard/docs/FLOWGUARD_SPEC.md)
for the full design.

Started from the Databricks workshop repo
[databricks-solutions/data-intelligence-for-smarter-community](https://github.com/databricks-solutions/data-intelligence-for-smarter-community);
`flowguard/app/server/{config,sql,genie}.py` are copied unchanged from it under the
Databricks license ([LICENSE.md](LICENSE.md), [NOTICE.md](NOTICE.md)).
