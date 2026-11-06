# Gold features

The hourly ingestion job rebuilds the Gold features and labels after Silver
normalization. Each row is one forecast example: one bidding zone, one delivery
interval, and one auction series. For the selected 15-minute training series, the
issue time is 10:00 Europe/Brussels on the previous local day. The row predicts
that interval’s price; it does not mean “15 minutes from now.” A full next-day
curve uses one row per 15-minute interval per zone (normally 96 rows per zone; 92
or 100 when daylight-saving time changes the local day). Labels are stored separately.

| Table | Contents |
| --- | --- |
| `day_ahead_features` | Historical inputs; target price excluded. |
| `day_ahead_price_labels` | Latest known price and revision per zone, interval, and auction series. |
| `day_ahead_training_data` | View joining features to their labels at that target grain. |

Labels are rebuilt from `silver_prices_current`. When a newly ingested revision
becomes current, the label value changes on the next Gold run. The label is an
outcome, never a predictor.

| Feature family | Columns | Definition |
| --- | --- | --- |
| Calendar | `calendar_hour_local`, `calendar_day_of_week_local`, `calendar_month_local`, `calendar_is_weekend` | Delivery time in the bidding zone's local timezone. |
| Calendar cycles | `calendar_time_of_day_*`, `calendar_day_of_week_*`, `calendar_month_*` | Sine/cosine encodings for recurring time patterns. |
| Prior prices | `price_lag_24h_eur_mwh`, `price_lag_168h_eur_mwh` | Same auction series and exact delivery timestamp 24 or 168 elapsed hours earlier. |
| Prior-price presence and provenance | `price_lag_*_present`, `price_lag_*_source_first_seen_at_utc` | Whether a lag value exists in Silver's current view, plus when the selected source version was downloaded. |

`forecast_issue_time_utc` uses the configured provisional 10:00 `Europe/Brussels`
cutoff on the day before local delivery. A lag points to the same auction series
and delivery clock time 24 or 168 elapsed hours earlier, so its delivery interval
precedes the target. The builder uses the latest Silver current value for that
past interval, including backfilled history. It assumes that value was available
by the forecast cutoff; `*_source_first_seen_at_utc` records download time, not
original publication time. Historical corrections are not reconstructed as of
each issue time, so a later correction could appear in an older example. This is
a deliberate approximation because original historical arrival times are absent
from the backfill.

This version builds retrospective examples from intervals already present in
Silver prices. It does not yet create tomorrow’s future feature rows or write a
forecast curve. A one-week-ahead model will need issue-time snapshots for target
intervals up to seven days ahead, using only information available at each issue time.

`target_*` columns identify the delivery interval and auction series; they do not
contain the price label and are metadata for joins, not automatic predictor inputs.
`feature_set_version` identifies the definitions, and
`feature_row_key` is a stable hash of the row grain. The job rebuilds these Gold
outputs from Silver on each run, so reruns are deterministic for unchanged source
data. Historical corrections are reflected only after the source is fetched again.

`day_ahead_training_data` joins by zone, target interval, resolution, and auction
series. Select model predictors explicitly and use `label_price_eur_mwh` as the
target; do not use label revision or observation metadata as predictors. Labels
are latest-known prices, not certified final settlements.

Weather, load, generation, and flow features are intentionally not included yet.
They can be added as typed `weather_*`, `load_*`, `generation_*`, or `flow_*`
columns after their source vintages are shown to be available at issue time.
