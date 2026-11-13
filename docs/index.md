# EU Electricity Forecast

A Databricks project for forecasting day-ahead electricity prices from ENTSO-E data.

## Pipeline

~~~mermaid
flowchart TB
    subgraph INGESTION[Running data pipeline]
        direction LR
        A[ENTSO-E API] --> B[Landing volume] --> C[Bronze]
    end
    subgraph FEATURES[Historical training data]
        direction LR
        D[Silver] --> E[Gold features and labels]
    end
    INGESTION --> FEATURES
~~~

See the [end-to-end MLOps flow](architecture/mlops.md) for training, the model
registry, environment delivery, inference, and monitored retraining. It marks
what runs today and what remains planned.

## Status

| Work | Status |
| --- | --- |
| Coverage survey for DE_LU, FR, NL, LT | Completed; target auction and publication delays need follow-up |
| Poller, backfill, immutable landing | Implemented and run in Databricks |
| Scheduled poll, Bronze, Silver | Running in the project catalog |
| Gold features and labels | Running; feature set 2.0.0 includes historical price lags |
| Global model training | Prior rolling run predates the 168-hour baseline; redeploy and rerun to evaluate it |
| Model registration and promotion | Planned |
| CI/CD and environment isolation | Planned; current dev/prod targets share the project catalog |
| Inference, monitoring, retraining | Planned |

The MVP predicts next-day auction prices before publication. Historical replay will
demonstrate streaming behavior. See [architecture](architecture/index.md),
[ingestion guide](entsoe-ingestion.md), and [implementation plan](implementation-plan.md).

Run the docs locally with make docs; validate them with make docs-build.
