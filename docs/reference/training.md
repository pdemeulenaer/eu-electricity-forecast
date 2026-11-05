# Model training

One row represents the price for one 15-minute delivery interval in one zone. Its
forecast is issued at 10:00 Europe/Brussels on the previous local day, so the lead
time is roughly 14–38 hours. The goal is a full next-day price curve: normally 96
interval predictions per zone (92 or 100 when daylight-saving time changes the
local day). This is not a forecast just 15 minutes ahead. The current pipeline
trains and evaluates on historical examples; future feature rows and forecast
writing are not implemented yet.

The experiment selects one 15-minute auction series for each of `DE_LU`, `FR`,
`NL`, and `LT`, then trains one global LightGBM model with zone as a categorical
input. For `DE_LU`, sequence 1 (SDAC) is selected; sequence 2 (EXAA) is a
different auction. It compares LightGBM with a simple seasonal-median baseline
(zone, weekday, and time of day). Price labels and revision metadata are not model
inputs.

Data is split by delivery time, in chronological order: first 70% for training,
next 15% for validation, final 15% for the held-out test. The same time cutoffs
apply to all zones, so a delivery timestamp cannot appear in multiple splits.
Both model and baseline are scored with MAE and RMSE overall and by zone, month,
and price regime. The test period is the final comparison; do not use it to tune
the model.

Inspect series coverage and price-lag availability in
`src/notebooks/model_training_experiment.ipynb`. Reusable code is in
`eu_electricity_forecast.training`; the `day_ahead_training` bundle job runs it
on serverless and is unscheduled. Runs log metrics and source Delta versions to
MLflow but do not register a model.

The first experiment (2026-11-05) trained on 105,015 rows and tested on 22,715.
Test MAE was 91.80 €/MWh for LightGBM and 91.50 for the seasonal baseline, so
LightGBM did not beat the baseline in this run. [View the MLflow run](https://dbc-b0577817-50e1.cloud.databricks.com/ml/experiments/4359392245988199/runs/852d07c337274a43b2c1df2cf65ca763).

A one-week-ahead model remains a sensible target. It will require training
examples with issue times up to seven days before delivery, plus weather and
other forecast inputs matched to the version available at each issue time.
