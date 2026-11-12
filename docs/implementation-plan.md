# Implementation plan

Build a Databricks pipeline from ENTSO-E ingestion through forecasting, monitoring,
and controlled retraining. Detailed layer responsibilities are in the
[architecture guide](architecture/index.md).

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
| 6 | Baselines, global model, MLflow experiment | Weekly rolling evaluation implemented locally; workspace run pending |
| 7 | Scoring and delayed-label reconciliation | Planned |
| 8 | Drift and quality monitoring | Planned |
| 9 | Retraining gates and model promotion | Planned |
| 10 | Replay demo, CI, and operational readiness | Planned |

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
The manual training job now runs fixed-parameter weekly rolling backtests across
the available history. Verify its first workspace run and compare results by
fold and zone. Keep the model as an experiment; the training job does not
register it.
Later CI/CD can register a challenger and promote it only after evaluation against
the Champion (or an initial acceptance gate when no Champion exists). Use the
notebook for interactive exploration; model and evaluation code is in
`src/eu_electricity_forecast/training.py`. The manual `day_ahead_training` wheel
job runs the same entry point for repeatable evaluation. Keep it unscheduled until
monitoring and Champion/Challenger CI/CD promotion gates are implemented.

### 7. Score and evaluate
Write immutable predictions linked to the model and feature snapshot. Join labels
only after they mature; retain revised labels separately and make reruns idempotent.

### 8. Monitor
Track ingestion health, freshness, coverage, feature drift, and rolling error on
mature labels. Calibrate thresholds by replay; drift alone does not promote a model.

### 9. Retrain and promote
Trigger a job from fresh monitoring metrics. Train and compare a candidate on a
recent chronological holdout; promote only if aggregate and per-zone gates pass.
Record decisions and preserve rollback to the previous Champion.

### 10. Demo and production checks
Replay historical data by availability time, then demonstrate drift, evaluation,
and promotion or rejection. Add tests and CI for parser, revisions, DST, leakage,
idempotency, delayed labels, and rollback. Validate workspace permissions,
network egress, monitoring availability, service identity, retention, and cost.

## Suggested delivery slices

1. Finish source contract, deploy ingestion, and validate Bronze/Silver.
2. Build historical features, baselines, and model evaluation.
3. Add incremental scoring and delayed-label evaluation.
4. Add monitoring, retraining gates, replay, and production checks.
