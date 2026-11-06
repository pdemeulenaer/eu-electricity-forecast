# Operations

## Local checks

~~~sh
make docs
make docs-build
uv run pytest
~~~

## Databricks ingestion job

The deployed hourly job polls ENTSO-E for the latest six hours, then runs Auto
Loader, Silver normalization, and the Gold feature rebuild in the
`eu_electricity_forecast` catalog. The Gold task overwrites the feature snapshot,
including historical 24-hour and 168-hour price lags from Silver's current view.

For future bundle changes, validate the target and confirm catalog/volume
permissions and the ENTSO-E secret are available to the job identity. See the
[ingestion guide](../entsoe-ingestion.md) for file layout and replay behavior.

Run the four-zone training and evaluation job manually with
`databricks bundle run day_ahead_training -t dev --profile dev`. It logs an MLflow
experiment and model artifact; it does not register or promote a model.
