# Operations

## Local checks

~~~sh
make docs
make docs-build
uv run pytest
~~~

## Databricks ingestion job

The bundle defines an hourly job: poll ENTSO-E for the latest six hours, then run
Auto Loader with an available-now trigger. It writes to a Unity Catalog Volume and
an append-only Bronze Delta table. The first workspace run succeeded in the original
workspace catalog/personal schema. The bundle is being updated to use the project
catalog and layer schemas; existing landing files are not moved automatically.

Before deploying the updated bundle, review the plan and confirm catalog/volume
permissions and the ENTSO-E secret are available to the job identity. Validate first. See the
[ingestion guide](../entsoe-ingestion.md) for file layout and replay behavior.
