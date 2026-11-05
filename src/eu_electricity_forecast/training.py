"""Train and evaluate the first global day-ahead price model."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from typing import Any

TARGET = "label_price_eur_mwh"
EXPECTED_ZONES = {"DE_LU", "FR", "NL", "LT"}
PREDICTORS = [
    "bidding_zone",
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
    "price_lag_24h_eur_mwh",
    "price_lag_168h_eur_mwh",
    "price_lag_24h_eur_mwh_available",
    "price_lag_168h_eur_mwh_available",
]
REQUIRED = [
    "bidding_zone",
    "target_interval_start_utc",
    "target_resolution_minutes",
    "target_auction_series_key",
    "calendar_hour_local",
    "calendar_day_of_week_local",
    TARGET,
]


def _validate_series_selection(series_by_zone: Mapping[str, str]) -> None:
    """Require one explicitly selected day-ahead price series for each project zone."""
    if not isinstance(series_by_zone, Mapping):
        raise TypeError("Series selection must be a JSON object mapping bidding zones to auction-series keys.")
    selected_zones = set(series_by_zone)
    missing = sorted(EXPECTED_ZONES - selected_zones)
    unexpected = sorted(selected_zones - EXPECTED_ZONES)
    if missing or unexpected:
        raise ValueError(
            "The global model must include exactly DE_LU, FR, NL, and LT. "
            f"Missing zones: {missing or 'none'}; unexpected zones: {unexpected or 'none'}."
        )
    invalid = sorted(zone for zone, key in series_by_zone.items() if not isinstance(key, str) or not key.strip())
    if invalid:
        raise ValueError(f"Provide a non-empty target_auction_series_key for each zone; invalid: {invalid}.")


def target_coverage(spark, table_name: str):
    """Summarize available target series in Spark without collecting all training rows."""
    from pyspark.sql import functions as F

    frame = spark.table(table_name)
    return (
        frame.groupBy(
            "bidding_zone",
            "target_resolution_minutes",
            "target_auction_type",
            "target_auction_category",
            "target_market_agreement_id",
            "target_contract_type",
            "target_auction_series_key",
        )
        .agg(
            F.count("*").alias("row_count"),
            F.min("target_interval_start_utc").alias("first_delivery_utc"),
            F.max("target_interval_start_utc").alias("last_delivery_utc"),
            F.sum(F.col("price_lag_24h_eur_mwh_available").cast("int")).alias("rows_with_24h_lag"),
            F.sum(F.col("price_lag_168h_eur_mwh_available").cast("int")).alias("rows_with_168h_lag"),
        )
        .orderBy("target_resolution_minutes", "target_auction_type", "bidding_zone")
    )


def load_training_frame(
    spark,
    table_name: str,
    series_by_zone: Mapping[str, str],
    resolution_minutes: int,
    source_delta_versions: Mapping[str, int] | None = None,
):
    """Load a version-pinned feature/label join for only the selected target series."""
    from functools import reduce

    from pyspark.sql import functions as F

    _validate_series_selection(series_by_zone)
    versions = source_delta_versions or gold_source_versions(spark, table_name)
    parts = table_name.split(".")
    catalog, schema, _ = parts
    feature_table = f"{catalog}.{schema}.day_ahead_features"
    label_table = f"{catalog}.{schema}.day_ahead_price_labels"
    features = spark.sql(f"SELECT * FROM {_quote_table(feature_table)} VERSION AS OF {versions[feature_table]}").alias("f")
    labels = spark.sql(f"SELECT * FROM {_quote_table(label_table)} VERSION AS OF {versions[label_table]}").alias("l")
    frame = features.join(
        labels,
        (F.col("f.bidding_zone") == F.col("l.bidding_zone"))
        & (F.col("f.target_interval_start_utc") == F.col("l.target_interval_start_utc"))
        & (F.col("f.target_resolution_minutes") == F.col("l.target_resolution_minutes"))
        & (F.col("f.target_auction_series_key") == F.col("l.target_auction_series_key")),
        "inner",
    ).select("f.*", F.col("l.label_price_eur_mwh").alias("label_price_eur_mwh"))
    frame = frame.where(F.col("target_resolution_minutes") == int(resolution_minutes))
    selections = [
        (F.col("bidding_zone") == zone) & (F.col("target_auction_series_key") == series_key)
        for zone, series_key in series_by_zone.items()
    ]
    frame = frame.where(reduce(lambda left, right: left | right, selections))
    columns = list(dict.fromkeys(REQUIRED + PREDICTORS + ["feature_set_version"]))
    return frame.select(*[name for name in columns if name in frame.columns]).toPandas()


def gold_source_versions(spark, training_view: str):
    """Return Delta versions for the feature and label tables behind the training view."""
    parts = training_view.split(".")
    if len(parts) != 3:
        raise ValueError("Training view must be a three-part catalog.schema.table name.")
    catalog, schema, _ = parts
    versions = {}
    for table_name in (f"{catalog}.{schema}.day_ahead_features", f"{catalog}.{schema}.day_ahead_price_labels"):
        row = spark.sql(f"DESCRIBE HISTORY {_quote_table(table_name)} LIMIT 1").first()
        if row is None:
            raise ValueError(f"No Delta history found for {table_name}.")
        versions[table_name] = int(row["version"])
    return versions


def _quote_table(table_name: str) -> str:
    parts = table_name.split(".")
    if len(parts) != 3 or any(not part or "`" in part for part in parts):
        raise ValueError(f"Expected a simple three-part Unity Catalog name; got {table_name!r}.")
    return ".".join(f"`{part}`" for part in parts)


def _validate_and_prepare(frame, series_by_zone: Mapping[str, str], resolution_minutes: int):
    import pandas as pd

    _validate_series_selection(series_by_zone)
    missing = sorted(set(REQUIRED) - set(frame.columns))
    if missing:
        raise ValueError(f"Training data is missing required columns: {missing}")
    data = frame.copy()
    data["target_interval_start_utc"] = pd.to_datetime(data["target_interval_start_utc"], utc=True)
    data[TARGET] = pd.to_numeric(data[TARGET], errors="coerce")
    data = data.dropna(subset=["target_interval_start_utc", TARGET, "bidding_zone"])
    data = data[data["target_resolution_minutes"].astype(int) == int(resolution_minutes)]
    if series_by_zone:
        valid = data["bidding_zone"].map(series_by_zone).eq(data["target_auction_series_key"])
        data = data[valid]
    if data.empty:
        raise ValueError("No labelled rows remain after applying the target series and resolution filters.")
    missing_zones = sorted(set(series_by_zone) - set(data["bidding_zone"].unique()))
    if missing_zones:
        raise ValueError(f"Selected auction series have no labelled rows for zones: {missing_zones}")
    key = ["bidding_zone", "target_interval_start_utc", "target_resolution_minutes", "target_auction_series_key"]
    duplicates = int(data.duplicated(key, keep=False).sum())
    if duplicates:
        raise ValueError(f"Found {duplicates} rows with duplicate training keys: {key}")
    return data.sort_values("target_interval_start_utc").reset_index(drop=True)


def chronological_split(frame, train_fraction: float = 0.70, validation_fraction: float = 0.15):
    """Split by shared delivery timestamps so one interval never crosses partitions."""
    times = frame["target_interval_start_utc"].drop_duplicates().sort_values().reset_index(drop=True)
    if len(times) < 10:
        raise ValueError(f"Need at least 10 distinct delivery timestamps; found {len(times)}.")
    train_end = max(1, min(len(times) - 2, int(len(times) * train_fraction)))
    validation_end = max(train_end + 1, min(len(times) - 1, int(len(times) * (train_fraction + validation_fraction))))
    train_cutoff = times.iloc[train_end]
    validation_cutoff = times.iloc[validation_end]
    train = frame[frame["target_interval_start_utc"] < train_cutoff].copy()
    validation = frame[
        (frame["target_interval_start_utc"] >= train_cutoff)
        & (frame["target_interval_start_utc"] < validation_cutoff)
    ].copy()
    test = frame[frame["target_interval_start_utc"] >= validation_cutoff].copy()
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("Chronological split produced an empty partition.")
    return {"train": train, "validation": validation, "test": test}, {
        "train_end_exclusive_utc": train_cutoff.isoformat(),
        "validation_end_exclusive_utc": validation_cutoff.isoformat(),
    }


def _features(frame, zone_categories):
    import pandas as pd

    selected = [name for name in PREDICTORS if name in frame.columns]
    if "bidding_zone" not in selected:
        raise ValueError("bidding_zone is required as the global model's categorical feature.")
    result = frame[selected].copy()
    result["bidding_zone"] = result["bidding_zone"].astype(pd.CategoricalDtype(categories=zone_categories))
    return result, selected


def _seasonal_baseline(train, rows):
    """Use train-only median price by zone, local weekday, and delivery time."""
    import pandas as pd

    keys = ["bidding_zone", "calendar_day_of_week_local", "calendar_hour_local"]
    train_keys = train[keys].copy()
    row_keys = rows[keys].copy()
    train_keys["_target"] = train[TARGET].to_numpy()
    seasonal = train_keys.groupby(keys, dropna=False)["_target"].median()
    zone_median = train.groupby("bidding_zone")[TARGET].median()
    global_median = float(train[TARGET].median())
    index = pd.MultiIndex.from_frame(row_keys)
    predictions = seasonal.reindex(index).to_numpy(dtype=float)
    missing = pd.isna(predictions)
    if missing.any():
        predictions[missing] = rows.loc[missing, "bidding_zone"].map(zone_median).fillna(global_median).to_numpy()
    return predictions


def _metrics(actual, predicted):
    import numpy as np

    errors = np.asarray(actual, dtype=float) - np.asarray(predicted, dtype=float)
    return {"mae": float(np.mean(np.abs(errors))), "rmse": float(np.sqrt(np.mean(np.square(errors))))}


def _group_metrics(scored, group_column):
    groups = {}
    for group_value, group in scored.groupby(group_column, dropna=False):
        groups[str(group_value)] = {
            "rows": len(group),
            "model": _metrics(group[TARGET], group["model_prediction"]),
            "seasonal_baseline": _metrics(group[TARGET], group["seasonal_baseline_prediction"]),
        }
    return groups


def run_experiment(
    frame,
    *,
    series_by_zone: Mapping[str, str],
    resolution_minutes: int,
    experiment_path: str,
    run_name: str = "global-lightgbm-baseline",
    lightgbm_params: Mapping[str, Any] | None = None,
    source_table: str = "unknown",
    source_delta_versions: Mapping[str, int] | None = None,
    code_revision: str | None = None,
):
    """Fit the seasonal baseline and LightGBM, evaluate chronologically, and log to MLflow."""
    import lightgbm as lgb
    import mlflow
    import mlflow.lightgbm

    data = _validate_and_prepare(frame, series_by_zone, resolution_minutes)
    partitions, cutoffs = chronological_split(data)
    train = partitions["train"]
    feature_cols = [name for name in PREDICTORS if name in data.columns]
    if len(train) < 100:
        raise ValueError(f"Need at least 100 training rows for a meaningful first experiment; found {len(train)}.")

    params = {"n_estimators": 400, "learning_rate": 0.04, "num_leaves": 31, "random_state": 42, "verbosity": -1}
    params.update(dict(lightgbm_params or {}))
    model = lgb.LGBMRegressor(**params)
    zone_categories = sorted(train["bidding_zone"].dropna().unique().tolist())
    train_x, feature_cols = _features(train, zone_categories)
    model.fit(train_x, train[TARGET], categorical_feature=["bidding_zone"])

    predictions = {}
    all_metrics = {}
    high_price_threshold = max(0.0, float(train[TARGET].quantile(0.95)))
    for split_name, split_frame in partitions.items():
        split_x, _ = _features(split_frame, zone_categories)
        model_values = model.predict(split_x)
        baseline_values = _seasonal_baseline(train, split_frame)
        predictions[split_name] = split_frame[
            ["bidding_zone", "target_interval_start_utc", TARGET, "target_auction_series_key"]
        ].copy()
        predictions[split_name]["model_prediction"] = model_values
        predictions[split_name]["seasonal_baseline_prediction"] = baseline_values
        scored = predictions[split_name].copy()
        scored["month_local"] = split_frame["calendar_month_local"].to_numpy()
        scored["price_regime"] = "ordinary"
        scored.loc[scored[TARGET] < 0, "price_regime"] = "negative"
        scored.loc[scored[TARGET] > high_price_threshold, "price_regime"] = "high"
        all_metrics[split_name] = {
            "model": _metrics(split_frame[TARGET], model_values),
            "seasonal_baseline": _metrics(split_frame[TARGET], baseline_values),
            "rows": len(split_frame),
            "by_zone": _group_metrics(scored, "bidding_zone"),
            "by_month": _group_metrics(scored, "month_local"),
            "by_price_regime": _group_metrics(scored, "price_regime"),
        }

    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(experiment_path)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params(
            {
                "model": "lightgbm.LGBMRegressor",
                "resolution_minutes": int(resolution_minutes),
                "zones": ",".join(sorted(series_by_zone)),
                "source_table": source_table,
                "code_revision": code_revision or os.environ.get("GIT_COMMIT", "unknown"),
                "feature_columns": ",".join(feature_cols),
                "train_end_exclusive_utc": cutoffs["train_end_exclusive_utc"],
                "validation_end_exclusive_utc": cutoffs["validation_end_exclusive_utc"],
                "high_price_threshold_from_train": high_price_threshold,
                **{f"series_{zone}": key for zone, key in series_by_zone.items()},
                **params,
            }
        )
        for split_name, metrics in all_metrics.items():
            for model_name in ("model", "seasonal_baseline"):
                for metric_name, value in metrics[model_name].items():
                    mlflow.log_metric(f"{split_name}_{model_name}_{metric_name}", value)
            mlflow.log_metric(f"{split_name}_rows", metrics["rows"])
            for dimension in ("by_zone", "by_month", "by_price_regime"):
                for group_value, group_metrics in metrics[dimension].items():
                    metric_group = group_value.replace(".", "_").replace("-", "_")
                    for model_name in ("model", "seasonal_baseline"):
                        for metric_name, value in group_metrics[model_name].items():
                            mlflow.log_metric(
                                f"{split_name}_{dimension}_{metric_group}_{model_name}_{metric_name}", value
                            )
        mlflow.log_dict(
            {
                "target": TARGET,
                "feature_set_versions": sorted(data.get("feature_set_version", []).dropna().unique().tolist())
                if "feature_set_version" in data
                else [],
                "split_cutoffs": cutoffs,
                "series_by_zone": dict(series_by_zone),
                "source_table": source_table,
                "source_delta_versions": dict(source_delta_versions or {}),
                "code_revision": code_revision or os.environ.get("GIT_COMMIT", "unknown"),
                "high_price_threshold_from_train": high_price_threshold,
                "metrics": all_metrics,
            },
            "experiment_summary.json",
        )
        mlflow.lightgbm.log_model(model, name="model")
        return {
            "run_id": run.info.run_id,
            "metrics": all_metrics,
            "cutoffs": cutoffs,
            "predictions": predictions,
            "feature_importance": dict(zip(feature_cols, model.feature_importances_.tolist())),
        }


def main() -> None:
    from pyspark.sql import SparkSession

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, help="Three-part Gold training view name")
    parser.add_argument("--series-by-zone", required=True, help="JSON map from bidding zone to target auction-series key")
    parser.add_argument("--resolution-minutes", type=int, required=True)
    parser.add_argument("--experiment-path", required=True)
    parser.add_argument("--run-name", default="global-lightgbm-baseline")
    parser.add_argument("--code-revision", default=os.environ.get("GIT_COMMIT", "unknown"))
    args = parser.parse_args()
    series_by_zone = json.loads(args.series_by_zone)
    if not isinstance(series_by_zone, dict):
        raise TypeError("--series-by-zone must be a JSON object, for example '{\"FR\":\"series-key\"}'.")
    spark = SparkSession.builder.getOrCreate()
    source_versions = gold_source_versions(spark, args.table)
    frame = load_training_frame(spark, args.table, series_by_zone, args.resolution_minutes, source_versions)
    result = run_experiment(
        frame,
        series_by_zone=series_by_zone,
        resolution_minutes=args.resolution_minutes,
        experiment_path=args.experiment_path,
        run_name=args.run_name,
        source_table=args.table,
        source_delta_versions=source_versions,
        code_revision=args.code_revision,
    )
    print(json.dumps({key: value for key, value in result.items() if key not in {"predictions", "feature_importance"}}, sort_keys=True))


if __name__ == "__main__":
    main()
