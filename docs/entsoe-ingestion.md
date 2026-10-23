# ENTSO-E ingestion

Configuration is in config/entsoe.json. It defines zones, EICs, query parameters,
units, expected resolutions, and a provisional 10:00 Brussels issue time.

## Credentials

Request a Transparency Platform token using
[ENTSO-E's instructions](https://transparencyplatform.zendesk.com/hc/en-us/articles/12845911031188-How-to-get-security-token).
For local use, put it in the ignored .env file as ENTSOE_API_TOKEN. Databricks
reads it from secret scope entsoe, key api-token. Do not commit or pass the token
in command arguments.

## Survey

Install dependencies with uv sync --dev and run:

~~~sh
uv run entsoe-survey \
  --start 2024-10-01T00:00:00Z \
  --end 2026-10-01T00:00:00Z \
  --include-flows \
  --output reports/entsoe-coverage.json
~~~

The survey queried prices, load, generation, forecasts, and configured flows for
DE_LU, FR, NL, and LT. It reports interval counts, resolutions, units, series and
auction identifiers, and empty responses. It does not infer coverage percentages
or publication delays. Recurring polls are needed to measure first-seen delay.

The sample found two distinct DE_LU price auctions; choose the training target
before modeling. Flow responses were sparse. Missing intervals are not zero.

## Backfill and scheduled polling

Backfill uses an inclusive UTC start and exclusive end:

~~~sh
uv run entsoe-ingest \
  --mode backfill \
  --start 2024-10-01T00:00:00Z \
  --end 2025-10-01T00:00:00Z \
  --landing-dir data/entsoe_landing
~~~

The poll command requests the latest six hours. It retries transient failures,
paces requests, and rechecks overlap for revisions. Completed backfill windows
are skipped on rerun; use --requery-completed to check them again.

The bundle defines a manual `entsoe_historical_backfill` job. After deployment,
run it with `databricks bundle run entsoe_historical_backfill -t dev`. It starts at
`entsoe_backfill_start` (default `2024-10-01T00:00:00Z`) and runs through the
current time. The hourly job then processes landed files through Bronze and Silver.
Completed query windows are skipped on rerun unless `--requery-completed` is used.

The REST API supports historical queries, but each request is limited to one year
and 100 matching documents. The client starts with 364-day windows and halves a
range when ENTSO-E rejects it. For larger archives, use ENTSO-E's asynchronous
[Data Repository](https://www.entsoe.eu/Documents/MC%20documents/Transparency%20Platform/MOP/00_ENTSO-E%20Manual%20of%20Procedures_V2R1.pdf).
See the current [API rejection limits](https://transparencyplatform.zendesk.com/hc/en-us/articles/15854332833300-Rejection-of-Request).

Historical backfill is useful for price labels and replay. It cannot recover when
each value first became available: `first_seen_at_utc` is the download time for
backfilled records. Use conservative publication cutoffs for historical features;
recurring polling measures actual availability delays going forward.

## Landing and timing

The Volume contains content-addressed XML under raw/, parsed JSONL under data/,
request status under metadata/, and Auto Loader state under _autoloader/. JSONL
records are immutable and include interval, product, zone, value, source revision,
first-seen time, and lineage.

The source document creation time is not the original publication time. The
10:00 D-1 issue time is provisional; day-ahead forecast products published later
must not be used as features at that cutoff. Use only source vintages available
when each forecast is issued.

See the [architecture](architecture/index.md) for Bronze and Silver schemas.
