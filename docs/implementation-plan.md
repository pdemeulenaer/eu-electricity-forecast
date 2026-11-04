# Implementation plan

Build a Databricks pipeline from ENTSO-E ingestion through forecasting, monitoring,
and controlled retraining. Detailed layer responsibilities are in the
[architecture guide](architecture/index.md).

## Decisions

- Forecast day-ahead prices before publication; use historical replay for the demo.
- Start with DE_LU, FR, NL, LT, then train on zones with adequate common coverage.
- Predict absolute EUR/MWh with one global LightGBM model.
- Preserve native resolution; handle the transition to 15-minute market intervals
  from October 2025 explicitly.
- Use only input vintages available at forecast issue time. Keep actual delivery
  measurements and later-published forecasts out of features.
- Treat Gold as the consumption layer for both curated feature tables and
  predictions, evaluation, and monitoring outputs.

## Progress

| Step | Work | Status |
| --- | --- | --- |
| 1 | Rename starter and validate development bundle | Rename complete; first workspace run succeeded |
| 2 | Survey source coverage and define target | Survey run; auction choice and first-seen delays remain |
| 3 | Shared client, backfill, polling, immutable files | Implemented locally |
| 4 | Bronze ingestion and Silver normalization | Implemented locally; deployment to project catalog pending |
| 5 | Point-in-time features and leakage checks | Implemented locally; deployment and data checks pending |
| 6 | Baselines, global model, MLflow registration | Planned |
| 7 | Scoring and delayed-label reconciliation | Planned |
| 8 | Drift and quality monitoring | Planned |
| 9 | Retraining gates and model promotion | Planned |
| 10 | Replay demo, CI, and operational readiness | Planned |

The scheduled poll and Bronze job completed a first workspace run. Silver is now
implemented locally; deployment to the project catalog is still pending and will
not move existing data.
The coverage survey found two DE_LU auction series with different values and sparse flow data;
select the price target before training. Historical ENTSO-E queries do not prove
when a value first became available, so measure delays from recurring polls.

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
encodings and same-series 24-hour and 168-hour price lags. Lags require a source
`first_seen_at_utc` no later than issue time; backfilled observations therefore
remain unavailable for historical cutoffs. Add weather, load, or generation fields
only after their issue-time availability is established. Also build
`gold.day_ahead_price_labels` from the latest Silver price revisions and expose
`gold.day_ahead_training_data` as a join of feature rows to labels. The ingestion
job rebuilds these Gold objects after Silver normalization; keep the label out of
the predictor columns.

### 6. Train
Start with an offline experiment after the latest bundle is deployed and the
ingestion job has rebuilt Gold. First check `day_ahead_training_data` row counts,
date coverage, duplicates, missing labels, and coverage by zone, resolution, and
auction series. Choose one DE_LU auction series for the first run; do not combine
series with different price definitions. Confirm that the selected target and
cutoff are consistent across zones.

Use explicit predictors (calendar fields, bidding zone, and only eligible price
lags); exclude labels, target values, revision metadata, and fields unavailable at
the issue cutoff. Historical backfill timestamps make the 24-hour and 168-hour
lags unavailable for earlier examples, so quantify lag coverage first. If sparse,
run the first model as a calendar-and-zone baseline and treat it as a pipeline
smoke test; do not present it as a validated forecasting result.

Compare a seasonal-naive baseline with one global LightGBM regressor. Split by
delivery time, with shared chronological train, validation, and test windows across
zones; never randomly split rows. Report MAE/RMSE overall and by zone, auction,
resolution, month, and high/negative-price regime. Log the data window and Delta
versions, feature set, split dates, code revision, parameters, and metrics to
MLflow. Keep the first model as an experiment; register it in Unity Catalog only
after the held-out results and target definition are accepted. Then package the
experiment as a repeatable Databricks training job.

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
