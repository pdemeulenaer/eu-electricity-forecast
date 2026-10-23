# Silver normalization

The `entsoe-silver` task reads Bronze and writes typed, revision-preserving Delta
tables in the Silver schema. It is idempotent and sends invalid or unmapped rows to
`silver_quarantine`.

The base table keeps an observation when it is first published and when ENTSO-E
later corrects it. The `_current` view is a saved query that shows only the latest
correction for each observation; it does not store a second copy. For example, if
a price changes from 75 to 72 EUR/MWh, the table keeps both and the view shows 72.
“Latest” means the latest correction, not the newest delivery time.

Each Silver table has a `_current` view:

- `silver_prices_current`
- `silver_load_current`
- `silver_generation_current`
- `silver_forecasts_current`
- `silver_cross_border_flows_current`

Missing observations are not filled with zero. See the
[architecture](../architecture/index.md) for table grains.
