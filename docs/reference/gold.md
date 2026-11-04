# Gold features

The hourly ingestion job rebuilds `eu_electricity_forecast.gold.day_ahead_features`
after Silver normalization. Each row describes inputs for one issue time, zone,
delivery interval, and auction series. The target price is not copied into this
table.

| Feature family | Columns | Definition |
| --- | --- | --- |
| Calendar | `calendar_hour_local`, `calendar_day_of_week_local`, `calendar_month_local`, `calendar_is_weekend` | Delivery time in the bidding zone's local timezone. |
| Calendar cycles | `calendar_time_of_day_*`, `calendar_day_of_week_*`, `calendar_month_*` | Sine/cosine encodings for recurring time patterns. |
| Prior prices | `price_lag_24h_eur_mwh`, `price_lag_168h_eur_mwh` | Same auction series and exact delivery timestamp 24 or 168 elapsed hours earlier. |
| Prior-price availability | `price_lag_*_available`, `price_lag_*_first_seen_at_utc` | Whether the selected version was observed by the issue cutoff, and when it first arrived. |

`forecast_issue_time_utc` uses the configured provisional 10:00 `Europe/Brussels`
cutoff on the day before local delivery. The builder reads the Silver revision table,
not the `_current` view; a correction is eligible only after its own
`first_seen_at_utc`, and a lag is included only when that arrival is no later than
the cutoff. Backfill fetch time is not the original source arrival time, so lags
from historical backfill remain null for old issue times. As scheduled polling
gathers prior prices, eligible lag features can appear for new training examples.

This version builds retrospective rows for delivery intervals present in Silver
prices. It does not yet generate future intervals for live inference; that requires
an explicit delivery schedule and target-auction configuration.

`target_*` columns identify the delivery interval and auction series; they do not
contain the price label and are metadata for joins, not automatic predictor inputs.
`feature_set_version` identifies the definitions, and
`feature_row_key` is a stable hash of the row grain. The job overwrites the table from
Silver on each run, so reruns are deterministic for unchanged source data.

Weather, load, generation, and flow features are intentionally not included yet.
They can be added as typed `weather_*`, `load_*`, `generation_*`, or `flow_*`
columns after their source vintages are shown to be available at issue time.
