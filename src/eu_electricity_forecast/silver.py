"""Normalize Bronze observations into typed, revision-preserving Silver Delta tables."""
from __future__ import annotations

import argparse
import json

SILVER_ITEM_GROUPS = {
    "silver_prices": ("day_ahead_prices",),
    "silver_load": ("actual_load",),
    "silver_generation": ("actual_generation_per_type",),
    "silver_forecasts": (
        "day_ahead_load_forecast",
        "day_ahead_generation",
        "day_ahead_wind_solar_forecast",
    ),
    "silver_cross_border_flows": ("physical_flows",),
}
ITEM_TO_TABLE = {
    item: table for table, items in SILVER_ITEM_GROUPS.items() for item in items
}

FIELD_TYPES = {
    "timestamp_utc": "TIMESTAMP",
    "interval_end_utc": "TIMESTAMP",
    "bidding_zone": "STRING",
    "data_item": "STRING",
    "value": "DOUBLE",
    "unit": "STRING",
    "source_unit_code": "STRING",
    "source_currency_code": "STRING",
    "resolution_minutes": "INT",
    "source_document_id": "STRING",
    "source_revision": "BIGINT",
    "parser_version": "STRING",
    "published_at_utc": "TIMESTAMP",
    "first_seen_at_utc": "TIMESTAMP",
    "ingested_at_utc": "TIMESTAMP",
    "production_type": "STRING",
    "business_type": "STRING",
    "process_type": "STRING",
    "curve_type": "STRING",
    "market_agreement_id": "STRING",
    "auction_type": "STRING",
    "auction_category": "STRING",
    "contract_type": "STRING",
    "classification_sequence": "BIGINT",
    "source_series_id": "STRING",
    "in_domain_eic": "STRING",
    "out_domain_eic": "STRING",
    "in_bidding_zone_eic": "STRING",
    "out_bidding_zone_eic": "STRING",
    "from_zone": "STRING",
    "to_zone": "STRING",
    "source_record_key": "STRING",
    "source_record_version_key": "STRING",
    "source_xml_sha256": "STRING",
    "source_xml_path": "STRING",
    "request_start_utc": "TIMESTAMP",
    "request_end_utc": "TIMESTAMP",
    "run_id": "STRING",
    "_rescued_data": "STRING",
}

REQUIRED_FIELDS = (
    "timestamp_utc",
    "interval_end_utc",
    "bidding_zone",
    "data_item",
    "value",
    "resolution_minutes",
    "source_record_key",
    "source_record_version_key",
)


def silver_table_for_item(data_item: str) -> str | None:
    """Return the Silver table for a known ENTSO-E data item."""
    return ITEM_TO_TABLE.get(data_item)


def _qualified(catalog: str, schema: str, name: str) -> str:
    return f"{catalog}.{schema}.{name}"


def _typed_observations(source):
    from pyspark.sql.functions import expr, lit

    available = set(source.columns)
    fields = []
    for name, data_type in FIELD_TYPES.items():
        if name in available:
            fields.append(expr(f"try_cast({name} AS {data_type})").alias(name))
        else:
            fields.append(lit(None).cast(data_type).alias(name))
    return source.select(*fields)


def _classify_rows(observations):
    from pyspark.sql.functions import col, concat_ws, lit, when

    checks = [
        (col("timestamp_utc").isNull(), "invalid_timestamp"),
        (col("interval_end_utc").isNull(), "invalid_interval_end"),
        (col("interval_end_utc") <= col("timestamp_utc"), "non_positive_interval"),
        (col("bidding_zone").isNull() | (col("bidding_zone") == ""), "missing_bidding_zone"),
        (col("data_item").isNull() | (col("data_item") == ""), "missing_data_item"),
        (col("value").isNull(), "invalid_value"),
        (col("resolution_minutes").isNull() | (col("resolution_minutes") <= 0), "invalid_resolution"),
        (col("source_record_key").isNull() | (col("source_record_key") == ""), "missing_source_record_key"),
        (
            col("source_record_version_key").isNull() | (col("source_record_version_key") == ""),
            "missing_source_record_version_key",
        ),
        (~col("data_item").isin(*ITEM_TO_TABLE), "unmapped_data_item"),
    ]
    reason = concat_ws(";", *(when(condition, lit(label)) for condition, label in checks))
    return observations.withColumn("_invalid_reason", reason)


def _merge_insert_only(spark, frame, table: str, key: str) -> None:
    from delta.tables import DeltaTable

    if not spark.catalog.tableExists(table):
        frame.limit(0).write.format("delta").mode("overwrite").saveAsTable(table)
    condition = f"target.{key} = source.{key}"
    (
        DeltaTable.forName(spark, table)
        .alias("target")
        .merge(frame.alias("source"), condition)
        .whenNotMatchedInsertAll()
        .execute()
    )


def _create_current_view(spark, catalog: str, schema: str, table: str) -> None:
    name = _qualified(catalog, schema, table)
    view = _qualified(catalog, schema, f"{table}_current")
    columns = ", ".join(spark.table(name).columns)
    spark.sql(
        f"""
        CREATE OR REPLACE VIEW {view} AS
        SELECT {columns}
        FROM (
          SELECT *,
            ROW_NUMBER() OVER (
              PARTITION BY source_record_key
              ORDER BY source_revision DESC NULLS LAST,
                       published_at_utc DESC NULLS LAST,
                       first_seen_at_utc DESC NULLS LAST,
                       source_record_version_key DESC
            ) AS _revision_rank
          FROM {name}
        ) ranked
        WHERE _revision_rank = 1
        """
    )


def run_silver(*, catalog: str, bronze_schema: str, silver_schema: str, input_catalog: str | None = None) -> None:
    """Idempotently type Bronze rows, preserve versions, quarantine invalid rows, and refresh views."""
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import coalesce, col, concat_ws, lit, sha2

    spark = SparkSession.builder.getOrCreate()
    bronze = _qualified(input_catalog or catalog, bronze_schema, "bronze_entsoe_observations")
    source = spark.table(bronze)
    classified = _classify_rows(_typed_observations(source))
    valid = classified.where(col("_invalid_reason") == "").drop("_invalid_reason")
    invalid = (
        classified.where(col("_invalid_reason") != "")
        .withColumnRenamed("_invalid_reason", "quarantine_reason")
        .withColumn(
            "quarantine_id",
            sha2(
                concat_ws(
                    "|",
                    coalesce(col("source_record_version_key"), lit("")),
                    coalesce(col("source_xml_sha256"), lit("")),
                    col("quarantine_reason"),
                ),
                256,
            ),
        )
    )

    _merge_insert_only(
        spark,
        invalid,
        _qualified(catalog, silver_schema, "silver_quarantine"),
        "quarantine_id",
    )
    for table, data_items in SILVER_ITEM_GROUPS.items():
        rows = valid.where(col("data_item").isin(*data_items))
        _merge_insert_only(spark, rows, _qualified(catalog, silver_schema, table), "source_record_version_key")
        _create_current_view(spark, catalog, silver_schema, table)
        print(json.dumps({"table": table, "data_items": data_items, "status": "merged"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--environment", choices=("dev", "qa", "prod"))
    parser.add_argument("--production-catalog", default="eu_electricity_forecast_prod")
    parser.add_argument("--input-catalog", help="Read Bronze from this catalog; outputs still use --catalog.")
    parser.add_argument("--bronze-schema", required=True)
    parser.add_argument("--silver-schema", required=True)
    args = parser.parse_args()
    if args.environment:
        from eu_electricity_forecast.catalog_policy import validate_source_catalog

        validate_source_catalog(
            environment=args.environment,
            output_catalog=args.catalog,
            input_catalog=args.input_catalog or args.catalog,
            production_catalog=args.production_catalog,
        )
    run_silver(catalog=args.catalog, bronze_schema=args.bronze_schema, silver_schema=args.silver_schema, input_catalog=args.input_catalog)


if __name__ == "__main__":
    main()
