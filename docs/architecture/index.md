# Architecture

The pipeline predicts next-day auction prices before publication. Historical replay
uses the same path to demonstrate streaming and retraining.

The [end-to-end MLOps flow](mlops.md) connects the data pipeline to training,
model registration, code delivery, inference, and monitoring, with current and
planned components marked.

## Medallion layers

| Layer | Contents | Status |
| --- | --- | --- |
| Landing | Immutable XML, JSONL observations, and request metadata in a Volume | Running in the project catalog |
| Bronze | Append-only, source-shaped observations | Running in the project catalog |
| Silver | Typed prices, load, generation, forecasts, and flows | Running in the project catalog |
| Gold | Forecast features, predictions, evaluation, and monitoring data | Features and labels running; prediction and monitoring outputs planned |

Gold is the business-consumption layer. It contains model inputs and outputs in
separate tables. Historical price lags use an explicit availability assumption;
they are not exact historical source snapshots.

## Table shapes

Bronze currently writes to bronze_entsoe_observations. Auto Loader infers and evolves
its schema. Rows contain interval, zone, product, value, resolution, source revision,
timing, market dimensions, and lineage fields. Unknown fields go to _rescued_data.

Silver tables:

| Table | Row grain |
| --- | --- |
| silver_prices | Zone, auction, delivery interval, revision |
| silver_load | Zone, delivery interval, revision |
| silver_generation | Zone, production type, interval, revision |
| silver_forecasts | Zone, forecast vintage, delivery interval |
| silver_cross_border_flows | Directed border, interval, revision |

Silver keeps corrected versions of observations in its tables. A `_current` view
shows the latest correction for each observation without storing another copy. For
example, if a price is corrected from 75 to 72 EUR/MWh, the table keeps both values
and the view shows 72. Invalid or unmapped rows go to `silver_quarantine`; missing
flow values remain missing.

Gold tables:

| Table | Row grain | Use / status |
| --- | --- | --- |
| day_ahead_features | Issue time, zone, target interval, target auction | Historical training rows; future inference rows to follow |
| day_ahead_price_labels | Zone, target interval, target auction | Latest known target price and revision metadata |
| day_ahead_training_data | Feature row joined to its target label | Training view; label excluded from predictor columns |
| price_predictions | Issue time, zone, target interval, auction, model version | Forecast consumers; planned |
| forecast_evaluation | Prediction identity and matured label | MAE/RMSE and label tracking; planned |
| monitoring_metrics | Metric window and zone/model slice | Drift and retraining decisions; planned |

`gold.day_ahead_features` is one wide row per forecast issue, bidding zone,
delivery interval, and auction series. It contains the following versioned inputs:

| Columns | Meaning |
| --- | --- |
| `calendar_*` | Delivery hour, weekday, month, weekend flag, and cyclic encodings, calculated in the zone's local time. |
| `price_lag_24h_eur_mwh` | Price for the same auction series and delivery time 24 elapsed hours earlier. |
| `price_lag_168h_eur_mwh` | Price for the same auction series and delivery time 168 elapsed hours earlier. |
| `price_lag_*_present` | Whether that prior interval has a price value in Silver's current view. |
| `price_lag_*_source_first_seen_at_utc` | When the selected source version was downloaded; for backfill this is not the original publication time. |
| `target_*` | Target interval and auction identifiers used to keep rows distinct; no target price value is included. |

The configured issue time is 10:00 on the prior local delivery date, in
`Europe/Brussels`; it remains provisional. Target rows and lag values use Silver's
`_current` view. The lag intervals precede the target and are treated as known by
the issue cutoff. This allows historical backfill to supply lag features, with the
assumption that corrections to old prices are rare. `price_lag_*_source_first_seen_at_utc`
is provenance only: backfill time is not original publication time, and the table
does not reconstruct the exact historical version available at each cutoff.

This first build creates retrospective training rows for delivery intervals found in
Silver prices. It does not yet generate future delivery intervals for live inference;
that needs an explicit interval schedule and target-auction configuration.

Add future inputs as typed, prefixed columns such as `weather_*`, `load_*`, or
`generation_*` only after their vintages can be shown to arrive before issue time.
Keep the target label out of the feature table. `target_*` fields identify the target
for joining and grouping; treat them as metadata, not predictor inputs, unless they
are independently known at issue time. The training view joins features to the
latest known label by zone, target interval, resolution, and auction series. New
source data still flows through
Landing/Bronze/Silver before it is joined into Gold. A Unity Catalog Delta table can
later be registered for Feature Engineering; the row key and issue time support
training lookups that need point-in-time guarantees.

## Unity Catalog layout

Use catalog `eu_electricity_forecast` with four schemas: `landing`, `bronze`,
`silver`, and `gold`. The managed volume `landing.entsoe_landing` holds immutable
source files and Auto Loader state; Bronze observations go to
`bronze.bronze_entsoe_observations`. The Silver tables and Gold feature, label, and
training-view objects use their matching schemas.

The hourly ingestion job rebuilds Gold features after Silver normalization.
Dev and prod currently reference this same catalog. Separate environment catalogs
and a registered model in Gold are [planned](mlops.md#code-delivery-and-environments-planned);
the four-schema structure stays the same.

See Databricks guidance on [medallion architecture](https://docs.databricks.com/gcp/en/lakehouse/medallion),
[Unity Catalog organization](https://docs.databricks.com/aws/en/lakehouse-architecture/data-governance/best-practices),
and [feature tables](https://docs.databricks.com/aws/en/machine-learning/feature-store/uc/feature-tables-uc).
