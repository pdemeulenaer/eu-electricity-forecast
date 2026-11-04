# EU Electricity Forecast

A Databricks project for forecasting day-ahead electricity prices from ENTSO-E data.

## Pipeline

~~~mermaid
flowchart TB
    subgraph INGESTION[Ingestion]
        direction LR
        A[ENTSO-E API] --> B[Poll and replay]
        B --> C[Immutable landing files]
        C --> D[Auto Loader Bronze]
    end
    subgraph FORECAST[Features and forecasting]
        direction LR
        E[Silver observations] --> F[Gold feature tables]
        F --> G[Global price model]
        G --> H[Gold prediction log]
    end
    subgraph MONITOR[Monitoring and retraining]
        direction LR
        I[Delayed labels] --> J[Gold evaluation and monitoring]
        J --> K[Train and evaluate candidate]
        K --> L[Promote model]
    end
    D --> E
    H --> I
    L -. New version .-> G
~~~

## Status

| Work | Status |
| --- | --- |
| Coverage survey for DE_LU, FR, NL, LT | Completed; target auction and publication delays need follow-up |
| Poller, backfill, immutable landing | Implemented locally |
| Scheduled poll, Bronze, Silver | Implemented locally; project-catalog deployment pending |
| Gold features, labels, and training view | Implemented locally; deploy and verify in Databricks |
| Model, monitoring, retraining | Planned |

The MVP predicts next-day auction prices before publication. Historical replay will
demonstrate streaming behavior. See [architecture](architecture/index.md),
[ingestion guide](entsoe-ingestion.md), and [implementation plan](implementation-plan.md).

Run the docs locally with make docs; validate them with make docs-build.
