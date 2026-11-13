# Implementation plan

Build a Databricks pipeline from ENTSO-E ingestion through forecasting, monitoring,
and controlled retraining. Detailed layer responsibilities are in the
[architecture guide](architecture/index.md). The
[end-to-end MLOps diagram](architecture/mlops.md) shows how the planned jobs connect.

## Decisions

- Forecast day-ahead prices before publication; use historical replay for the demo.
- Train one global model on DE_LU, FR, NL, and LT together, using a selected
  day-ahead auction series for each zone.
- Predict absolute EUR/MWh with one global LightGBM model.
- Preserve native resolution; handle the transition to 15-minute market intervals
  from October 2025 explicitly.
- Use historical price lags from the latest Silver current values, assuming they
  were known at issue time; later corrections are not reconstructed as-of each
  issue. For other inputs, require a vintage available by issue time.
- Treat Gold as the consumption layer for both curated feature tables and
  predictions, evaluation, and monitoring outputs.

## Progress

| Step | Work | Status |
| --- | --- | --- |
| 1 | Rename starter and validate development bundle | Rename complete; first workspace run succeeded |
| 2 | Survey source coverage and define target | Survey complete; selected auction series configured; historical publication delays remain unverified |
| 3 | Shared client, backfill, polling, immutable files | Implemented and run in Databricks |
| 4 | Bronze ingestion and Silver normalization | Running in the project catalog |
| 5 | Gold historical features and labels | Feature set 2.0.0 rebuilt and checked in Databricks |
| 6 | Baselines, global model, MLflow experiment | Prior rolling run predates the 168-hour baseline; redeploy and rerun to evaluate it |
| 7 | Environment isolation and CI/CD | Planned; dev/prod currently share the project catalog |
| 8 | Deployable candidate, registry, and promotion | Planned; rolling evaluation logs no deployable model |
| 9 | Future features, inference, and delayed-label evaluation | Planned |
| 10 | Data quality, drift, and performance monitoring | Planned |
| 11 | Controlled retraining and rollback | Planned |
| 12 | Replay demo and operational readiness | Planned |

The scheduled ingestion pipeline runs through Gold in the project catalog. Its
feature builder uses latest historical prices for 24-hour and 168-hour lags; the
backfill does not preserve original price publication times. Coverage found two
DE_LU auction series with different values; training selects Sequence 1 (SDAC).
Recurring polls can measure first-seen delays for new data, but not reconstruct
historical publication times.

## Step details

### 1. Rename and workspace baseline
Build the wheel, validate imports and the bundle, then run a development smoke test.
Check existing deployments before deploying the renamed bundle because resource
keys may create new resources rather than rename old ones.

### 2. Source contract
Confirm the target auction, issue cutoff, label maturity rule, units, resolution,
and revision handling. Keep UTC intervals and zone-local calendar time. Retain
negative prices and represent missing observations as missing.

### 3. Ingestion
Keep immutable source XML, JSONL observations, and token-free request metadata.
Use bounded backfills, retries, request pacing, completion markers, and overlap
polling. Test revisions, DST, acknowledgements, restart, and replay.

### 4. Bronze and Silver
Auto Loader processes landed files even if an API request failed; the job still
reports that poll failure. Silver casts fields, quarantines invalid rows, preserves
source revisions with idempotent merges, and exposes `_current` views. Deploy and
verify the tables in the project catalog.

### 5. Features
Create one wide `gold.day_ahead_features` snapshot keyed by issue time, zone,
delivery interval, and auction series. The first version adds local-calendar
encodings and same-series 24-hour and 168-hour price lags. Historical lags use the
latest Silver value for the prior delivery interval and assume it was available by
issue time; source first-seen timestamps record download time, not original release.
This accepts a small risk that a later correction appears in an older example.
Add weather, load, or generation fields
only after their issue-time availability is established. Also build
`gold.day_ahead_price_labels` from the latest Silver price revisions and expose
`gold.day_ahead_training_data` as a join of feature rows to labels. The ingestion
job rebuilds these Gold objects after Silver normalization; keep the label out of
the predictor columns.

### 6. Train
The first offline experiment is complete. It used 15-minute A01 prices in all
four zones, Sequence 1 (SDAC) for DE_LU, one global LightGBM model, and a
calendar-and-zone seasonal baseline. It used shared chronological splits and
feature set 2.0.0; results are in the [training reference](reference/training.md).
The manual training job runs fixed-parameter weekly rolling backtests across
the available history. The first workspace run completed before the 168-hour
naive benchmark was added. Next, redeploy and rerun, then compare results by fold
and zone. Add relative MAE against that weekly benchmark as a planned metric,
computed on the same rows where its lag is available. Keep the model as an
experiment; the training job does not register it.
Later CI/CD can register a challenger and promote it only after evaluation against
the Champion (or an initial acceptance gate when no Champion exists). Use the
notebook for interactive exploration; model and evaluation code is in
`src/eu_electricity_forecast/training.py`. The manual `day_ahead_training` wheel
job runs the same entry point for repeatable evaluation. Keep it unscheduled until
monitoring and Champion/Challenger CI/CD promotion gates are implemented.

## Next delivery order

The detailed [environment and release plan](environment-release-plan.md) covers
steps 7 and 8, including catalog access, CI checks, QA approval, and model promotion.

These steps are planned. Keep production schedules disabled until environment
isolation and the required checks are in place. Each delivery should work manually
in dev before its production automation is enabled.

| Step | Deliver | Completion check |
| --- | --- | --- |
| **7. Environments and CI/CD** | Separate dev/QA/prod catalogs, each with landing/bronze/silver/gold and ml_models schemas. Add branch-based CI and QA-to-production delivery with service identities and protected releases. | QA tests the proposed release and cannot write to prod. An approved, matching release reaches production. |
| **8. Candidate and registry** | Keep rolling backtests for policy evaluation. Add a candidate fit with packaged preprocessing, signature, dependencies, code revision, and pinned data versions. Register the QA candidate in Unity Catalog and record promotion decisions. | After approval and merge, the evaluated QA artifact is copied to prod and assigned `@prod`. The saved candidate reloads and reproduces predictions. The previous version remains available. |
| **9. Inference and evaluation** | Generate future quarter-hour rows without target prices. Save issue-time feature snapshots; score a pinned Champion version daily into Gold predictions. Join labels when available and refresh after corrections. | All four zones have a complete next-local-day curve: 92/96/100 slots as applicable. Repeating a run creates no duplicates or silent replacement of issued forecasts. Evaluation reports label coverage. |
| **10. Monitoring** | Add freshness, completeness, feature drift, and labelled MAE/RMSE by zone/model. Compare with seasonal and weekly benchmarks; add dashboards and alerts. | Replay demonstrates a missing-data alert, a feature-shift alert, and a performance alert. Sparse labels cannot trigger a false retraining decision. |
| **11. Retraining loop** | Add periodic and monitor-triggered candidate requests with a cooldown, minimum samples, and one active run. Route every candidate through the same evaluation and promotion gates. | A good candidate can be promoted, a bad one is rejected, and rollback restores the prior Champion. Retries do not create repeated promotions. |
| **12. Demo and operations** | Add a repeatable replay scenario, runbooks, retention, cost limits, and historical correction requerying. | Demonstrate ingestion → forecast → delayed evaluation → alert → candidate → promotion/rejection, with code, data, and model lineage. |

### Promotion gates

Agree thresholds before using the final evaluation period. Compare candidate,
Champion, and benchmarks on the same rows, with aggregate and per-zone checks.
Require all four zones, adequate label and lag coverage, acceptable MAE/RMSE, and
no unacceptable regional regression. When no Champion exists, use the benchmarks
and an initial acceptance gate. Relative MAE against the 168-hour benchmark remains
planned.

Tune hyperparameters on earlier rolling validation windows; keep a later period
untouched for final acceptance. Registering a version does not make it production:
promotion selects the approved artifact, without a hidden refit afterward.

### Operational safeguards

Keep issued forecast inputs and predictions separate from the historical Gold
table that is overwritten today. Apply live issue-time availability checks to
features; retain the documented historical lag assumption for backtests. Include
DST, auction identity, missing data, reruns, delayed labels, and rollback in checks.

Use data profiling where supported for managed statistics and project tables for
benchmark and delayed-label metrics. Historical demo dates need an explicit replay
clock and compatible metric refresh windows; downloading history today does not
recover its original publication timestamps.
