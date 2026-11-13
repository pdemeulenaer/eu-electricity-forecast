# Model training

## What time period does one forecast cover?

For example, a forecast issued for DE_LU at 10:00 on Monday covers every
15-minute interval on Tuesday:

```text
Brussels time:  Monday (D−1)                                  Tuesday (D)
                10:00                                         00:00       ...  23:45
                 │                                             │                │
                ISSUE ─────────────── 14 hours ──────────────→ slot 1  ·····→ slot 96
Delivery:                                                       00:00–00:15      23:45–00:00
                                                               96 prices, one per slot
```

The model predicts one price per slot, not one price for the whole day. In winter,
the first slot starts 14 hours after the cutoff and the last starts 37 hours 45
minutes after it. Lithuania is one local hour ahead of Brussels, so its delivery
slots start 13 to 36 hours 45 minutes after the same cutoff. A local delivery day
has 92, 96, or 100 quarter-hour slots when daylight-saving time changes it.

The current job only trains and evaluates on past days. Creating tomorrow’s 96
future feature rows, scoring them, and writing the forecast curve are still to be
implemented.

The experiment selects one 15-minute auction series for each of `DE_LU`, `FR`,
`NL`, and `LT`, then trains one global LightGBM model. It uses these inputs:

| Input | What it tells the model |
| --- | --- |
| Zone | Which bidding zone the target belongs to; treated as a category. |
| Calendar | Local delivery time, weekday, month, weekend flag, and cyclical encodings of time, weekday, and month. |
| Price history | Latest same-series price at the target time 24 and 168 hours earlier. |
| Lag presence | Whether each lag value exists in Silver's current view. Historical values are assumed to have been known by the cutoff; later corrections are not replayed as-of each date. |

There are no weather, load, generation, or flow inputs yet. For `DE_LU`, the
selected target is sequence 1 (SDAC); sequence 2 (EXAA) is a different auction.
The target price and its revision metadata are never model inputs.

One benchmark is the seasonal median: the median training price for the same
zone, local weekday, and delivery hour. It pools across months and years; a
Saturday target uses earlier Saturdays, not Friday’s price. If that group is
missing, it falls back to the zone median, then the global median. The second
benchmark copies the same-series price from 168 elapsed hours earlier. That is
the one-week-lag naive forecast. LightGBM receives the 168-hour lag as an input
along with calendar features; it must be evaluated against both benchmarks.

The training job uses weekly rolling-origin evaluation. It waits until 180 days
of history are available, fits one global model, and scores the next seven days.
It then advances one week, adds earlier labels to training, and fits again. The
default expanding window keeps all earlier rows; set `rolling_training_window_days`
to a positive value to restrict each fit to recent history. Test windows do not
overlap, and each model trains only on earlier delivery intervals.

Each weekly test window becomes training history for the next fit. The held-out
weeks together measure this weekly refit policy:

~~~mermaid
flowchart LR
    A[180-day warm-up] --> B[Train on past rows]
    B --> C[Score next 7 days]
    C --> D[Advance 7 days]
    D --> E[Add matured history]
    E --> B
    C --> F[Pool each held-out prediction once]
~~~

Pooled MAE and RMSE are computed from every held-out prediction across weeks and
zones, weighting each row equally. The report also includes a tougher weekly
naive benchmark: predict the target using its same-series price 168 elapsed
hours earlier. Because some rows may lack that lag, its coverage is reported,
and model and median-baseline scores are also computed on exactly the rows where
the weekly lag exists. Per-zone metrics and weekly fold scores remain available.
Relative MAE (`model MAE / weekly-baseline MAE`) is planned; it will use those
same lag-available rows, so values below 1 mean the model beats the benchmark.
The job logs fold predictions to `rolling_predictions.csv` in MLflow; the
notebook plots the three prediction series for one selected fold.

This estimates the performance of a policy that retrains each week; it is not
the score of one model held fixed for the whole history. The historical price-lag
availability assumption still applies. Treat this as an exploratory backtest,
and reserve a later untouched period for final evaluation before tuning or
promotion.

Reusable code is in `eu_electricity_forecast.training`; the unscheduled
`day_ahead_training` bundle job runs it on serverless. Rolling runs log metrics,
fold summaries, source Delta versions, and predictions to MLflow; they do not
register a model. The
`src/notebooks/model_training_experiment.ipynb` notebook loads the latest
completed run and plots actual prices, LightGBM predictions, the seasonal
median, and the 168-hour lag for one selected held-out week. It does not retrain
the model or require manual auction-series IDs.

The earlier single-split experiment on feature set 2.0.0 beat the seasonal
baseline on validation and test:

| Split | Rows | Model MAE | Baseline MAE | Model RMSE | Baseline RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| Validation | 22,518 | 31.65 | 53.24 | 40.92 | 61.11 |
| Test | 22,772 | 65.61 | 93.38 | 83.34 | 110.09 |

All metrics are in €/MWh. Test MAE was lower for LightGBM in each zone: DE_LU
62.25 vs 89.22, FR 62.20 vs 109.96, LT 77.30 vs 83.33, and NL 60.72 vs 91.24.
November was the hardest test month (MAE 109.95 vs 168.62). These results
describe one holdout only, not performance across seasons. Read new rolling
results from their MLflow run. The historical lag availability assumption
described in the [Gold reference](gold.md) also applies.

A one-week-ahead model remains a sensible target. It will require training
examples with issue times up to seven days before delivery, plus weather and
other forecast inputs matched to the version available at each issue time.

## Next: a deployable model

Rolling evaluation measures a refitting policy and currently logs no deployable
model. A separate candidate fit, Unity Catalog registration, and Champion
promotion gate are planned. See the [end-to-end MLOps flow](../architecture/mlops.md);
daily inference and monitoring will consume the approved model version.

Planned: log the package version, Git commit, and build identity for every
training run, and attach them to registered model versions. See the
[code version and model lineage plan](../environment-release-plan.md#code-version-and-model-lineage).
