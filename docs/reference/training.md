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
| Price history | Same-series price at the target time 24 and 168 hours earlier, when that price had arrived by the forecast cutoff. |
| Lag availability | Whether each lag was available at the cutoff. |

There are no weather, load, generation, or flow inputs yet. For `DE_LU`, the
selected target is sequence 1 (SDAC); sequence 2 (EXAA) is a different auction.
The target price and its revision metadata are never model inputs.

The comparison baseline is **not** yesterday’s price. It predicts the median
training price for the same zone, local weekday, and delivery time. Thus a
Saturday target uses historical Saturdays at that time, not Friday’s price. If
that group is missing, it falls back to the zone median, then the global median.
LightGBM receives the same calendar information plus the price lags, but more
inputs do not guarantee better predictions.

Data is split by delivery time, in chronological order: first 70% for training,
next 15% for validation, final 15% for the held-out test. The same time cutoffs
apply to all zones, so a delivery timestamp cannot appear in multiple splits.
For the 2026-11-05 run, training ended before 2026-07-08 13:45 UTC; validation
ran until 2026-09-06 18:00 UTC; the test began then and covered the remaining
available data through November. Counts were 105,015 train, 22,465 validation,
and 22,715 test rows.

This is one chronological holdout, not cross-validation. The test covers autumn,
not a full annual cycle, so this run does not establish performance across all
seasons. Both model and baseline are scored with MAE and RMSE overall and by zone,
month, and price regime. Do not use the test period to tune the model.

Inspect series coverage and price-lag availability in
`src/notebooks/model_training_experiment.ipynb`. Reusable code is in
`eu_electricity_forecast.training`; the `day_ahead_training` bundle job runs it
on serverless and is unscheduled. Runs log metrics and source Delta versions to
MLflow but do not register a model.

The first experiment (2026-11-05) trained on 105,015 rows and tested on 22,715.
Test MAE was 91.80 €/MWh for LightGBM and 91.50 for the seasonal baseline; RMSE
was 109.34 and 107.67 respectively. This single test period shows a small loss
for LightGBM, not why it happened or whether the gap is stable. Lag availability
and per-zone/month/price-regime results should be checked before drawing a
conclusion. [View the MLflow run](https://dbc-b0577817-50e1.cloud.databricks.com/ml/experiments/4359392245988199/runs/852d07c337274a43b2c1df2cf65ca763).

A lag-availability audit on 2026-11-06 found **0 of 105,015 training rows** and
**0 of 22,465 validation rows** had either lag available. In the current Gold
snapshot, 3,555 of 23,097 test rows (15.39%) have the 24-hour lag and 4,672
(20.23%) have the 168-hour lag. That snapshot has 382 more test rows than the
original run, so its test coverage rates are estimates for that run. The model
was fitted without observed lag values, while some test rows have them; rerun
without lag predictors or rebuild trustworthy historical point-in-time lags
before judging their value.

A one-week-ahead model remains a sensible target. It will require training
examples with issue times up to seven days before delivery, plus weather and
other forecast inputs matched to the version available at each issue time.
