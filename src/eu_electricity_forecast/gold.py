"""Build point-in-time day-ahead price features in the Gold schema."""

from __future__ import annotations

import argparse
import json

AUCTION_COLUMNS = (
    "bidding_zone",
    "unit",
    "source_unit_code",
    "source_currency_code",
    "business_type",
    "process_type",
    "production_type",
    "curve_type",
    "market_agreement_id",
    "auction_type",
    "auction_category",
    "contract_type",
    "classification_sequence",
    "in_domain_eic",
    "out_domain_eic",
    "in_bidding_zone_eic",
    "out_bidding_zone_eic",
)
FEATURE_SET_VERSION = "1.0.0"


def _series_key():
    from pyspark.sql.functions import coalesce, col, lit, sha2, struct, to_json

    return sha2(
        to_json(struct(*(coalesce(col(name).cast("string"), lit("")).alias(name) for name in AUCTION_COLUMNS))),
        256,
    )


def _with_price_lag(features, history, lag_hours: int):
    from pyspark.sql import Window
    from pyspark.sql.functions import col, expr, row_number

    feature_name = f"price_lag_{lag_hours}h_eur_mwh"
    observed_name = f"{feature_name}_first_seen_at_utc"
    t, h = features.alias("t"), history.alias("h")
    condition = (
        (col("t.bidding_zone") == col("h.bidding_zone"))
        & (col("t.target_auction_series_key") == col("h.auction_series_key"))
        & (col("h.timestamp_utc") == col("t.target_interval_start_utc") - expr(f"INTERVAL {lag_hours} HOURS"))
        & col("h.first_seen_at_utc").isNotNull()
        & (col("h.first_seen_at_utc") <= col("t.forecast_issue_time_utc"))
    )
    order = Window.partitionBy(col("t.feature_row_key")).orderBy(
        col("h.first_seen_at_utc").desc_nulls_last(),
        col("h.source_revision").desc_nulls_last(),
        col("h.source_record_version_key").desc_nulls_last(),
    )
    return (
        t.join(h, condition, "left")
        .withColumn("_lag_rank", row_number().over(order))
        .where(col("_lag_rank") == 1)
        .select(
            "t.*",
            col("h.value").cast("double").alias(feature_name),
            col("h.first_seen_at_utc").alias(observed_name),
        )
        .withColumn(f"{feature_name}_available", col(feature_name).isNotNull())
    )


def build_day_ahead_features(*, catalog: str, silver_schema: str, gold_schema: str, config: dict) -> None:
    """Rebuild Gold training snapshots using only price vintages seen by issue time.

    Historical target intervals are discovered from Silver day-ahead prices. The target
    price itself is used only to identify training rows; its value is never written to
    the feature table.
    """
    from itertools import chain

    from pyspark.sql import SparkSession
    from pyspark.sql.functions import (
        col,
        concat,
        cos,
        create_map,
        date_format,
        date_sub,
        dayofweek,
        element_at,
        from_utc_timestamp,
        hour,
        lit,
        minute,
        month,
        pmod,
        radians,
        sha2,
        sin,
        struct,
        to_date,
        to_json,
        to_timestamp,
        to_utc_timestamp,
    )

    spark = SparkSession.builder.getOrCreate()
    forecast = config["forecast"]
    issue_timezone = forecast["issue_timezone"]
    issue_time = forecast["issue_time_local"]
    source_table = f"{catalog}.{silver_schema}.silver_prices"
    target_table = f"{catalog}.{gold_schema}.day_ahead_features"
    zones = config["zones"]
    timezone_map = create_map(*chain.from_iterable((lit(zone), lit(spec["timezone"])) for zone, spec in zones.items()))

    prices = spark.table(source_table).where((col("data_item") == "day_ahead_prices") & (col("unit") == "EUR/MWh"))
    prices = prices.withColumn("auction_series_key", _series_key())
    # Read the version table (not its _current view) so each lag uses only revisions
    # that had actually arrived by the forecast issue time. Target rows define the
    # retrospective examples, not feature values.
    local_delivery = from_utc_timestamp(col("timestamp_utc"), element_at(timezone_map, col("bidding_zone")))
    issue_wall_time = to_timestamp(
        concat(
            date_format(date_sub(to_date(local_delivery), 1), "yyyy-MM-dd"),
            lit(f" {issue_time}:00"),
        )
    )
    targets = (
        prices.dropDuplicates(["source_record_key"])
        .withColumn("_zone_timezone", element_at(timezone_map, col("bidding_zone")))
        .withColumn("_local_delivery", from_utc_timestamp(col("timestamp_utc"), col("_zone_timezone")))
        .withColumn(
            "forecast_issue_time_utc",
            to_utc_timestamp(issue_wall_time, lit(issue_timezone)),
        )
        .withColumn("target_interval_start_utc", col("timestamp_utc"))
        .withColumn("target_interval_end_utc", col("interval_end_utc"))
        .withColumn("target_resolution_minutes", col("resolution_minutes"))
        .withColumn("target_auction_series_key", col("auction_series_key"))
        .withColumn(
            "calendar_hour_local", hour(col("_local_delivery")).cast("double") + minute(col("_local_delivery")) / 60.0
        )
        .withColumn("calendar_day_of_week_local", (pmod(dayofweek(col("_local_delivery")) + 5, lit(7)) + 1).cast("int"))
        .withColumn("calendar_month_local", month(col("_local_delivery")).cast("int"))
        .withColumn("calendar_is_weekend", col("calendar_day_of_week_local").isin(6, 7))
        .withColumn(
            "_minute_of_day",
            hour(col("_local_delivery")) * 60 + minute(col("_local_delivery")),
        )
        .withColumn("calendar_time_of_day_sin", sin(radians(col("_minute_of_day") * (360.0 / 1440.0))))
        .withColumn("calendar_time_of_day_cos", cos(radians(col("_minute_of_day") * (360.0 / 1440.0))))
        .withColumn(
            "calendar_day_of_week_sin",
            sin(radians((col("calendar_day_of_week_local") - 1) * (360.0 / 7.0))),
        )
        .withColumn(
            "calendar_day_of_week_cos",
            cos(radians((col("calendar_day_of_week_local") - 1) * (360.0 / 7.0))),
        )
        .withColumn(
            "calendar_month_sin",
            sin(radians((col("calendar_month_local") - 1) * (360.0 / 12.0))),
        )
        .withColumn(
            "calendar_month_cos",
            cos(radians((col("calendar_month_local") - 1) * (360.0 / 12.0))),
        )
        .withColumn("feature_set_version", lit(FEATURE_SET_VERSION))
        .withColumn(
            "feature_row_key",
            sha2(
                to_json(
                    struct(
                        "forecast_issue_time_utc",
                        "bidding_zone",
                        "target_interval_start_utc",
                        "target_resolution_minutes",
                        "target_auction_series_key",
                    )
                ),
                256,
            ),
        )
        .select(
            "feature_row_key",
            "forecast_issue_time_utc",
            "bidding_zone",
            "target_interval_start_utc",
            "target_interval_end_utc",
            "target_resolution_minutes",
            "target_auction_series_key",
            col("auction_type").alias("target_auction_type"),
            col("auction_category").alias("target_auction_category"),
            col("market_agreement_id").alias("target_market_agreement_id"),
            col("contract_type").alias("target_contract_type"),
            "feature_set_version",
            "calendar_hour_local",
            "calendar_day_of_week_local",
            "calendar_month_local",
            "calendar_is_weekend",
            "calendar_time_of_day_sin",
            "calendar_time_of_day_cos",
            "calendar_day_of_week_sin",
            "calendar_day_of_week_cos",
            "calendar_month_sin",
            "calendar_month_cos",
        )
    )
    history = prices.select(
        "bidding_zone",
        "auction_series_key",
        "timestamp_utc",
        "value",
        "first_seen_at_utc",
        "source_revision",
        "source_record_version_key",
    )
    features = _with_price_lag(targets, history, 24)
    features = _with_price_lag(features, history, 168)
    (features.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(target_table))
    spark.sql(
        f"COMMENT ON TABLE {target_table} IS "
        "'Point-in-time model inputs for day-ahead prices. Excludes target price labels.'"
    )
    print(
        json.dumps(
            {
                "table": target_table,
                "feature_set_version": FEATURE_SET_VERSION,
                "issue_timezone": issue_timezone,
                "issue_time_local": issue_time,
                "availability_rule": "first_seen_at_utc <= forecast_issue_time_utc",
                "status": "rebuilt",
            },
            sort_keys=True,
        )
    )


def main() -> None:
    from eu_electricity_forecast.entsoe import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--silver-schema", required=True)
    parser.add_argument("--gold-schema", required=True)
    args = parser.parse_args()
    build_day_ahead_features(
        catalog=args.catalog,
        silver_schema=args.silver_schema,
        gold_schema=args.gold_schema,
        config=load_config(),
    )


if __name__ == "__main__":
    main()
