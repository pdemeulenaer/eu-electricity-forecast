# Architecture

The pipeline predicts next-day auction prices before publication. Historical replay
uses the same path to demonstrate streaming and retraining.

~~~mermaid
flowchart TB
    A[ENTSO-E API] --> B[Poller and backfill]
    B --> C[Volume landing]
    C --> D[Bronze]
    D --> E[Silver]
    E --> F[Gold feature tables]
    F --> G[Global model]
    G --> H[Gold predictions]
    E --> I[Mature price labels]
    H --> I
    I --> J[Gold evaluation and monitoring]
    J --> G
~~~

## Medallion layers

| Layer | Contents | Status |
| --- | --- | --- |
| Landing | Immutable XML, JSONL observations, and request metadata in a Volume | First workspace run succeeded; relocation pending |
| Bronze | Append-only, source-shaped observations | Implemented; deployment to project catalog pending |
| Silver | Typed prices, load, generation, forecasts, and flows | Implemented locally; deployment pending |
| Gold | Point-in-time features, predictions, evaluation, and monitoring data | Feature table implemented locally; deployment pending |

Gold is the business-consumption layer. It contains both point-in-time model inputs
and model outputs; these are separate tables with different grains.

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

| Table | Row grain | Use |
| --- | --- | --- |
| day_ahead_features | Issue time, zone, target interval, target auction | Point-in-time training rows; future inference rows to follow |
| price_predictions | Issue time, zone, target interval, model version | Forecast consumers |
| forecast_evaluation | Prediction identity and matured label | MAE/RMSE and label tracking |
| monitoring_metrics | Metric window and zone/model slice | Drift and retraining decisions |

`gold.day_ahead_features` is one wide row per forecast issue, bidding zone,
delivery interval, and auction series. It contains the following versioned inputs:

| Columns | Meaning |
| --- | --- |
| `calendar_*` | Delivery hour, weekday, month, weekend flag, and cyclic encodings, calculated in the zone's local time. |
| `price_lag_24h_eur_mwh` | Price for the same auction series and delivery time 24 elapsed hours earlier. |
| `price_lag_168h_eur_mwh` | Price for the same auction series and delivery time 168 elapsed hours earlier. |
| `price_lag_*_available` | Whether that lag was observed by the issue cutoff. |
| `price_lag_*_first_seen_at_utc` | Arrival time of the exact source vintage used for the lag. |
| `target_*` | Target interval and auction identifiers used to keep rows distinct; no target price value is included. |

The configured issue time is 10:00 on the prior local delivery date, in
`Europe/Brussels`; it remains provisional. The builder reads Silver's revision table,
not its `_current` view, so a later correction cannot appear in an earlier snapshot.
Price lags qualify only when their `first_seen_at_utc` is at or before that cutoff.
ENTSO-E backfill timestamps reflect download time, so historical backfill values
will not pass old cutoffs, so those historical lag values remain null. Calendar
features are always available. This conservative rule avoids treating backfilled
or later revisions as if they had been known earlier.

This first build creates retrospective training rows for delivery intervals found in
Silver prices. It does not yet generate future delivery intervals for live inference;
that needs an explicit interval schedule and target-auction configuration.

Add future inputs as typed, prefixed columns such as `weather_*`, `load_*`, or
`generation_*` only after their vintages can be shown to arrive before issue time.
Keep the target label out of this table. `target_*` fields identify the target for
joining and grouping; treat them as metadata, not predictor inputs, unless they are
independently known at issue time. New source data still flows through
Landing/Bronze/Silver before it is joined into Gold. A Unity Catalog Delta table can
later be registered for Feature Engineering; the row key and issue time support
point-in-time training lookups.

## Unity Catalog layout

Use catalog `eu_electricity_forecast` with four schemas: `landing`, `bronze`,
`silver`, and `gold`. The managed volume `landing.entsoe_landing` holds immutable
source files and Auto Loader state; Bronze observations go to
`bronze.bronze_entsoe_observations`. The Silver tables and
`gold.day_ahead_features` use their matching schemas.

The first successful run wrote to the existing `workspace.pdemeulenaer` location.
The bundle will create the new catalog layout and direct subsequent ingestion there;
existing files and table data need to be copied or replayed separately before the old
location can be retired.

See Databricks guidance on [medallion architecture](https://docs.databricks.com/gcp/en/lakehouse/medallion),
[Unity Catalog organization](https://docs.databricks.com/aws/en/lakehouse-architecture/data-governance/best-practices),
and [feature tables](https://docs.databricks.com/aws/en/machine-learning/feature-store/uc/feature-tables-uc).
