---
hide:
  - toc
---

# End-to-end MLOps

This is the target flow for forecasting next-day prices in DE_LU, FR, NL, and LT.
Green boxes run today; blue dashed boxes are planned. Read the rows from top to
bottom. Code delivery is shown separately below. The same job definitions will run in
each isolated environment.

## Data, models, and forecasts

~~~mermaid
%%{init: {'flowchart': {'nodeSpacing': 20, 'rankSpacing': 30, 'padding': 10, 'subGraphTitleMargin': {'top': 5, 'bottom': 12}}}}%%
flowchart TB
    subgraph DATA["1 · Data pipeline"]
        direction LR
        API[ENTSO-E] --> LAND[Landing volume] --> BRONZE[Bronze] --> SILVER[Silver] --> GOLD[Gold features<br/>and labels]
    end
    subgraph TRAIN["2 · Training and model selection"]
        direction LR
        BACKTEST[Global model<br/>rolling evaluation] --> TRACK[MLflow<br/>tracking]
        TRACK --> FIT[Candidate fit<br/>and evaluation]
        FIT --> REG[Unity Catalog registry<br/>promotion gate]
    end
    subgraph SCORE["3 · Daily inference"]
        direction LR
        FUTURE[Future feature<br/>snapshots] --> PREDICT[Score with<br/>Champion version]
        PREDICT --> PREDICTIONS[Gold predictions<br/>and benchmarks]
        PREDICTIONS --> CONSUMERS[Forecast charts<br/>and consumers]
    end
    subgraph MONITOR["4 · Feedback and controlled retraining"]
        direction LR
        DRIFT[Data quality<br/>and feature drift] --> ALERT[Alert and check<br/>retraining conditions]
        PERFORMANCE[Join price labels<br/>MAE / RMSE] --> ALERT
        ALERT --> RETRAIN[Return to training<br/>with a new candidate]
    end
    DATA -->|Historical training rows| TRAIN
    TRAIN -->|Approved model| SCORE
    SCORE -->|Saved forecasts and inputs| MONITOR

    classDef current fill:#dcfce7,stroke:#15803d,color:#14532d,stroke-width:2px;
    classDef planned fill:#eff6ff,stroke:#2563eb,color:#1e3a8a,stroke-width:2px,stroke-dasharray:6 4;
    class API,LAND,BRONZE,SILVER,GOLD,BACKTEST,TRACK current;
    class FIT,REG,FUTURE,PREDICT,PREDICTIONS,CONSUMERS,DRIFT,PERFORMANCE,ALERT,RETRAIN planned;
~~~

The data job already builds historical Gold features and labels. The rolling
training job records experiments; it does **not** register a model or forecast
future days. The 168-hour benchmark is implemented locally and awaits a new
workspace run. See the [training reference](../reference/training.md).

**Tracking records an experiment. Registration gives a saved model a version.
Promotion chooses which version inference uses.** A registered candidate is a
Challenger; the approved production version is the Champion, selected by the
`@prod` alias. Models live in each catalog’s `ml_models` schema. Use Unity Catalog aliases,
rather than the old Staging/Production registry stages.
[Databricks model lifecycle](https://docs.databricks.com/aws/en/machine-learning/manage-model-lifecycle/)

The rolling job saves metrics and prediction files, not a deployable model. Add
a candidate fit that packages preprocessing, zone categories, feature columns,
dependencies, and an input/output signature. Evaluate that exact artifact against
both benchmarks and the existing Champion, if any, on the same reserved period.
Promotion keeps the evaluated artifact; it must not silently refit it.

## Code delivery and environments — planned

Feature changes pass CI before merging into `develop`. A pull request from
`develop` to `main` deploys and tests the proposed release in QA. Stakeholder
approval allows the pull request to merge; the resulting `main` commit is then
released to production. Dev deployments remain manual.

~~~mermaid
flowchart TB
    subgraph CHANGE["Feature development"]
        direction LR
        FEATURE[Feature branch] -->|Pull request| CI[CI checks]
        CI -->|Review and merge| DEVELOP[develop]
    end
    subgraph RELEASE["Release"]
        direction LR
        PR[develop → main pull request] --> QA[Deploy and test in QA]
        QA --> REVIEW[Stakeholder review]
        REVIEW -->|Approve and merge| MAIN[main]
        MAIN --> PROD[Deploy production<br/>and create version tag]
    end
    DEVELOP --> PR
    classDef default fill:#eff6ff,stroke:#2563eb,color:#1e3a8a,stroke-width:2px,stroke-dasharray:6 4;
~~~

Dev and QA jobs can read their own data or selected production data; they always
write to their own catalog. Production jobs read and write production. Unity
Catalog permissions must enforce these rules, not just bundle configuration.
The current dev and prod bundle targets share one catalog; QA does not exist yet.
See the [environment and release plan](../environment-release-plan.md) for the
proposed names, tests, approvals, and model registry flow.

## Forecasts and monitoring — planned

Daily batch inference will create the next local delivery day's quarter-hour
rows from Silver history at the issue cutoff (provisionally 10:00 Brussels time),
score all four zones, and retain the inputs used.
Tomorrow's target price is not needed to build those rows. Save the resolved
model version, feature version, issue time, auction, and delivery interval with
every forecast. Reruns must preserve issued forecasts.

| Check | What it detects | Response |
| --- | --- | --- |
| Data quality | Late ingestion, missing lags, duplicate or incomplete forecast curves | Alert; investigate or use an explicit fallback |
| Data drift | Feature distributions move from a seasonally relevant reference; PSI is one possible measure | Investigate; consider a new candidate |
| Model performance | Rolling MAE/RMSE deteriorate after labels arrive, by zone and model version | Investigate possible changed price relationships and other causes |

Quality and feature-drift checks run before labels arrive; performance checks
wait for published prices. An error increase can signal concept drift; it does not prove it. Report label
coverage and compare the model with the 168-hour benchmark on the same rows.
Relative MAE remains planned. Calibrate thresholds using replay; drift alone
must not promote a model.

Use Databricks data profiling (Lakehouse Monitoring) for feature and inference
statistics, with project evaluation tables for delayed labels and benchmarks.
Its inference profiles accept inputs, predictions, a model ID, and optional
labels. Confirm workspace availability and historical refresh limits first.
[Data profiling](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-quality-monitoring/data-profiling/)

The loop is **monitor → request candidate → evaluate → promote or reject**.
Require enough labelled examples, persistent breaches, a cooldown, and one
active retraining run. Keep the previous Champion for rollback. Follow the
[delivery order](../implementation-plan.md#next-delivery-order) for implementation.
