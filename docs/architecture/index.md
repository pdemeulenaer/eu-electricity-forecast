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
| Gold | Curated features, predictions, evaluation, and monitoring data | Planned |

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

Planned Gold tables:

| Table | Row grain | Use |
| --- | --- | --- |
| day_ahead_features | Issue time, zone, target interval | Training and inference inputs |
| price_predictions | Issue time, zone, target interval, model version | Forecast consumers |
| forecast_evaluation | Prediction identity and matured label | MAE/RMSE and label tracking |
| monitoring_metrics | Metric window and zone/model slice | Drift and retraining decisions |

Feature rows include predictors, issue cutoff, source availability/vintage, and a
stable snapshot key. Unity Catalog Feature Engineering can use a Delta table with
a primary key as a feature table.

## Unity Catalog layout

Use catalog `eu_electricity_forecast` with four schemas: `landing`, `bronze`,
`silver`, and `gold`. The managed volume `landing.entsoe_landing` holds immutable
source files and Auto Loader state; Bronze observations go to
`bronze.bronze_entsoe_observations`. Silver and Gold tables will be added in their
matching schemas.

The first successful run wrote to the existing `workspace.pdemeulenaer` location.
The bundle will create the new catalog layout and direct subsequent ingestion there;
existing files and table data need to be copied or replayed separately before the old
location can be retired.

See Databricks guidance on [medallion architecture](https://docs.databricks.com/gcp/en/lakehouse/medallion),
[Unity Catalog organization](https://docs.databricks.com/aws/en/lakehouse-architecture/data-governance/best-practices),
and [feature tables](https://docs.databricks.com/aws/en/machine-learning/feature-store/uc/feature-tables-uc).
