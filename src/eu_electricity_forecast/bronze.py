"""Ingest immutable ENTSO-E JSON files into an append-only Bronze Delta table."""
from __future__ import annotations

import argparse


def run_autoloader(*, landing_dir: str, catalog: str, schema: str) -> None:
    """Process all currently available landing files and checkpoint progress."""
    from pyspark.sql import SparkSession

    root = landing_dir.rstrip("/")
    source = f"{root}/data"
    state = f"{root}/_autoloader"
    table = f"{catalog}.{schema}.bronze_entsoe_observations"
    spark = SparkSession.builder.getOrCreate()

    observations = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.schemaLocation", f"{state}/schema")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .option("cloudFiles.inferColumnTypes", "true")
        .option("rescuedDataColumn", "_rescued_data")
        .load(source)
    )

    query = (
        observations.writeStream.format("delta")
        .option("checkpointLocation", f"{state}/checkpoint")
        .option("mergeSchema", "true")
        .trigger(availableNow=True)
        .toTable(table)
    )
    query.awaitTermination()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--landing-dir", required=True, help="UC Volume root containing the data/ prefix")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()
    run_autoloader(landing_dir=args.landing_dir, catalog=args.catalog, schema=args.schema)


if __name__ == "__main__":
    main()
