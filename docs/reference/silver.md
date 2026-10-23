# Silver normalization

The `entsoe-silver` task reads Bronze and writes typed, revision-preserving Delta
tables in the Silver schema. It is idempotent and sends invalid or unmapped rows to
`silver_quarantine`.

Each Silver table has a `_current` view selecting the latest source revision:

- `silver_prices_current`
- `silver_load_current`
- `silver_generation_current`
- `silver_forecasts_current`
- `silver_cross_border_flows_current`

Historical versions remain in the base tables. Missing observations are not filled
with zero. See the [architecture](../architecture/index.md) for table grains.
