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

The comparison baseline is **not** yesterday’s price. It predicts the median
training price for the same zone, local weekday, and delivery time. Thus a
Saturday target uses historical Saturdays at that time, not Friday’s price. If
that group is missing, it falls back to the zone median, then the global median.
LightGBM receives the same calendar information plus the price lags, but more
inputs do not guarantee better predictions.

Data is split chronologically by delivery time: 70% train, 15% validation, and
15% test, using shared cutoffs across zones. The 2026-11-06 run used feature set
2.0.0 and contained 105,287 train rows, 22,518 validation rows, and 22,772 test
rows. Training ended before 2026-07-09 06:45 UTC; validation ended before
2026-09-07 14:30 UTC; the test covered the remaining data through early November.

This is one autumn holdout, not cross-validation or a full seasonal evaluation.
The model and baseline are scored with mean absolute error (MAE) and root mean
squared error (RMSE), overall and by zone, month, and price regime. Do not use
the test period to tune the model.

Inspect series coverage and price-lag presence in
`src/notebooks/model_training_experiment.ipynb`. Reusable code is in
`eu_electricity_forecast.training`; the `day_ahead_training` bundle job runs it
on serverless and is unscheduled. Runs log metrics and source Delta versions to
MLflow but do not register a model.

The 2026-11-06 feature set 2.0.0 run beat the seasonal baseline on validation and
test:

| Split | Rows | Model MAE | Baseline MAE | Model RMSE | Baseline RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| Validation | 22,518 | 31.65 | 53.24 | 40.92 | 61.11 |
| Test | 22,772 | 65.61 | 93.38 | 83.34 | 110.09 |

All metrics are in €/MWh. Test MAE was lower for LightGBM in each zone: DE_LU
62.25 vs 89.22, FR 62.20 vs 109.96, LT 77.30 vs 83.33, and NL 60.72 vs 91.24.
November was the hardest test month (MAE 109.95 vs 168.62). These results are
promising for this split, but do not establish performance across seasons. The
historical lag availability assumption described in the [Gold reference](gold.md)
also applies to this run. [View the MLflow run](https://dbc-b0577817-50e1.cloud.databricks.com/ml/experiments/4359392245988199/runs/3208d46a10134982a5ed8295a5b5939c).

A one-week-ahead model remains a sensible target. It will require training
examples with issue times up to seven days before delivery, plus weather and
other forecast inputs matched to the version available at each issue time.
