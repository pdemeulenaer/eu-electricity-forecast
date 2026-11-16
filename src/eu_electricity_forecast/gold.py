"""Build historical day-ahead price features in the Gold schema."""

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
FEATURE_SET_VERSION = "2.0.0"


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
    observed_name = f"{feature_name}_source_first_seen_at_utc"
    t, h = features.alias("t"), history.alias("h")
    condition = (
        (col("t.bidding_zone") == col("h.bidding_zone"))
        & (col("t.target_auction_series_key") == col("h.auction_series_key"))
        & (col("h.timestamp_utc") == col("t.target_interval_start_utc") - expr(f"INTERVAL {lag_hours} HOURS"))
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
        .withColumn(f"{feature_name}_present", col(feature_name).isNotNull())
    )


def build_day_ahead_features(*, catalog: str, silver_schema: str, gold_schema: str, config: dict, input_catalog: str | None = None) -> None:
    """Rebuild Gold training snapshots using current historical prices for lags.

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
    source_table = f"{input_catalog or catalog}.{silver_schema}.silver_prices"
    current_table = f"{input_catalog or catalog}.{silver_schema}.silver_prices_current"
    target_table = f"{catalog}.{gold_schema}.day_ahead_features"
    zones = config["zones"]
    timezone_map = create_map(*chain.from_iterable((lit(zone), lit(spec["timezone"])) for zone, spec in zones.items()))

    prices = spark.table(source_table).where((col("data_item") == "day_ahead_prices") & (col("unit") == "EUR/MWh"))
    prices = prices.withColumn("auction_series_key", _series_key())
    current_prices = spark.table(current_table).where(
        (col("data_item") == "day_ahead_prices") & (col("unit") == "EUR/MWh")
    )
    current_prices = current_prices.withColumn("auction_series_key", _series_key())
    # Historical backfills do not preserve original arrival times. Use the current
    # Silver price for each lag and treat it as known at issue time; this assumes
    # historical price corrections are rare. Keep auction series separate.
    local_delivery = from_utc_timestamp(col("timestamp_utc"), element_at(timezone_map, col("bidding_zone")))
    issue_wall_time = to_timestamp(
        concat(
            date_format(date_sub(to_date(local_delivery), 1), "yyyy-MM-dd"),
            lit(f" {issue_time}:00"),
        )
    )
    targets = (
        current_prices
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
    history = current_prices.select(
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
        "'Historical model inputs for day-ahead prices. Price lags use latest Silver values as historical availability proxies. Excludes target price labels.'"
    )
    print(
        json.dumps(
            {
                "table": target_table,
                "feature_set_version": FEATURE_SET_VERSION,
                "issue_timezone": issue_timezone,
                "issue_time_local": issue_time,
                "lag_availability_policy": "latest_silver_current_value_assumed_known_at_issue_time",
                "status": "rebuilt",
            },
            sort_keys=True,
        )
    )


def build_day_ahead_price_labels(*, catalog: str, silver_schema: str, gold_schema: str, input_catalog: str | None = None) -> None:
    """Rebuild one latest-known label per zone, delivery interval, and auction series."""
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import col

    spark = SparkSession.builder.getOrCreate()
    source = f"{input_catalog or catalog}.{silver_schema}.silver_prices_current"
    target = f"{catalog}.{gold_schema}.day_ahead_price_labels"
    current_prices = spark.table(source).where(
        (col("data_item") == "day_ahead_prices") & (col("unit") == "EUR/MWh")
    )
    labels = (
        current_prices.withColumn("target_auction_series_key", _series_key())
        .select(
            "bidding_zone",
            col("timestamp_utc").alias("target_interval_start_utc"),
            col("interval_end_utc").alias("target_interval_end_utc"),
            col("resolution_minutes").alias("target_resolution_minutes"),
            "target_auction_series_key",
            col("value").cast("double").alias("label_price_eur_mwh"),
            col("source_revision").alias("label_source_revision"),
            col("published_at_utc").alias("label_published_at_utc"),
            col("first_seen_at_utc").alias("label_first_seen_at_utc"),
            col("source_record_key").alias("label_source_record_key"),
            col("source_record_version_key").alias("label_source_record_version_key"),
        )
    )
    labels.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
    spark.sql(
        f"COMMENT ON TABLE {target} IS "
        "'Latest known ENTSO-E day-ahead price labels by zone, interval, and auction series.'"
    )
    print(json.dumps({"table": target, "status": "rebuilt"}, sort_keys=True))


def build_day_ahead_training_view(*, catalog: str, gold_schema: str) -> None:
    """Join historical features to their latest known target price labels."""
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    features = f"{catalog}.{gold_schema}.day_ahead_features"
    labels = f"{catalog}.{gold_schema}.day_ahead_price_labels"
    view = f"{catalog}.{gold_schema}.day_ahead_training_data"
    spark.sql(
        f"""
        CREATE OR REPLACE VIEW {view} AS
        SELECT
          f.*,
          l.label_price_eur_mwh,
          l.label_source_revision,
          l.label_published_at_utc,
          l.label_first_seen_at_utc,
          l.label_source_record_version_key
        FROM {features} f
        INNER JOIN {labels} l
          ON f.bidding_zone = l.bidding_zone
         AND f.target_interval_start_utc = l.target_interval_start_utc
         AND f.target_resolution_minutes = l.target_resolution_minutes
         AND f.target_auction_series_key = l.target_auction_series_key
        """
    )
    print(json.dumps({"view": view, "status": "refreshed"}, sort_keys=True))


def main() -> None:
    from eu_electricity_forecast.entsoe import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--environment", choices=("dev", "qa", "prod"))
    parser.add_argument("--production-catalog", default="eu_electricity_forecast_prod")
    parser.add_argument("--input-catalog", help="Read Silver from this catalog; outputs still use --catalog.")
    parser.add_argument("--silver-schema", required=True)
    parser.add_argument("--gold-schema", required=True)
    args = parser.parse_args()
    if args.environment:
        from eu_electricity_forecast.catalog_policy import validate_source_catalog

        validate_source_catalog(
            environment=args.environment,
            output_catalog=args.catalog,
            input_catalog=args.input_catalog or args.catalog,
            production_catalog=args.production_catalog,
        )
    build_day_ahead_features(
        catalog=args.catalog,
        silver_schema=args.silver_schema,
        gold_schema=args.gold_schema,
        config=load_config(),
        input_catalog=args.input_catalog,
    )
    build_day_ahead_price_labels(catalog=args.catalog, silver_schema=args.silver_schema, gold_schema=args.gold_schema, input_catalog=args.input_catalog)
    build_day_ahead_training_view(catalog=args.catalog, gold_schema=args.gold_schema)


if __name__ == "__main__":
    main()
