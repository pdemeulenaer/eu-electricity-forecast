# Getting started

Requirements: Python 3.12 and uv. A Databricks workspace and ENTSO-E token are
needed for source ingestion or bundle deployment.

Install dependencies and serve the docs:

~~~sh
uv sync --dev --group docs
make docs
~~~

Open http://127.0.0.1:8000. Use PORT=8001 to choose another port. Run
make docs-build for a strict static build.

For API access and data commands, see the [ingestion guide](../entsoe-ingestion.md).
Store local credentials in the ignored .env file or use a Databricks secret. Never
commit credentials.
