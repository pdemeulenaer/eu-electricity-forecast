import pandas as pd

from eu_electricity_forecast.training import (
    PREDICTORS,
    TARGET,
    _weekly_lag_comparison,
    _weekly_lag_predictions,
    _validate_and_prepare,
    chronological_split,
    rolling_origin_splits,
)


def test_chronological_split_keeps_shared_timestamps_in_one_partition():
    rows = []
    for day in pd.date_range("2025-01-01", periods=20, freq="D", tz="UTC"):
        for zone in ("FR", "NL"):
            rows.append({"target_interval_start_utc": day, "bidding_zone": zone})
    frame = pd.DataFrame(rows)

    partitions, cutoffs = chronological_split(frame)

    assert set(partitions) == {"train", "validation", "test"}
    assert all(len(partitions[name]) > 0 for name in partitions)
    train_dates = set(partitions["train"]["target_interval_start_utc"])
    validation_dates = set(partitions["validation"]["target_interval_start_utc"])
    test_dates = set(partitions["test"]["target_interval_start_utc"])
    assert train_dates.isdisjoint(validation_dates)
    assert validation_dates.isdisjoint(test_dates)
    assert max(train_dates) < min(validation_dates) < min(test_dates)
    assert cutoffs["train_end_exclusive_utc"] < cutoffs["validation_end_exclusive_utc"]


def test_predictor_allowlist_excludes_labels_and_revision_metadata():
    assert "label_price_eur_mwh" not in PREDICTORS
    assert "label_source_revision" not in PREDICTORS
    assert "label_first_seen_at_utc" not in PREDICTORS
    assert "target_auction_series_key" not in PREDICTORS


def test_training_data_is_filtered_to_the_selected_series_per_zone():
    frame = pd.DataFrame(
        [
            {
                "bidding_zone": zone,
                "target_interval_start_utc": "2025-01-01T00:00:00Z",
                "target_resolution_minutes": 60,
                "target_auction_series_key": series,
                "calendar_hour_local": 1.0,
                "calendar_day_of_week_local": 3,
                "label_price_eur_mwh": 10.0,
            }
            for zone, series in (
                ("DE_LU", "de-lu-selected"),
                ("FR", "fr-selected"),
                ("NL", "nl-selected"),
                ("LT", "lt-selected"),
            )
        ]
        + [
            {
                "bidding_zone": "FR",
                "target_interval_start_utc": "2025-01-01T00:00:00Z",
                "target_resolution_minutes": 60,
                "target_auction_series_key": "fr-other-auction",
                "calendar_hour_local": 1.0,
                "calendar_day_of_week_local": 3,
                "label_price_eur_mwh": 999.0,
            }
        ]
    )

    selected = _validate_and_prepare(
        frame,
        {"DE_LU": "de-lu-selected", "FR": "fr-selected", "NL": "nl-selected", "LT": "lt-selected"},
        60,
    )

    assert len(selected) == 4
    assert set(selected["target_auction_series_key"]) == {
        "de-lu-selected",
        "fr-selected",
        "nl-selected",
        "lt-selected",
    }


def test_global_model_requires_all_four_project_zones():
    frame = pd.DataFrame(
        [
            {
                "bidding_zone": "FR",
                "target_interval_start_utc": "2025-01-01T00:00:00Z",
                "target_resolution_minutes": 60,
                "target_auction_series_key": "fr-selected",
                "calendar_hour_local": 1.0,
                "calendar_day_of_week_local": 3,
                "label_price_eur_mwh": 10.0,
            }
        ]
    )

    try:
        _validate_and_prepare(frame, {"FR": "fr-selected", "NL": "nl-selected"}, 60)
    except ValueError as exc:
        assert "exactly DE_LU, FR, NL, and LT" in str(exc)
    else:
        raise AssertionError("Expected incomplete zone selection to fail")


def test_rolling_origin_splits_use_past_training_and_non_overlapping_test_windows():
    frame = pd.DataFrame(
        {
            "target_interval_start_utc": pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC"),
            "value": range(400),
        }
    )

    folds = rolling_origin_splits(
        frame,
        minimum_train_days=180,
        test_window_days=7,
        step_days=7,
        resolution_minutes=1440,
    )

    assert len(folds) > 20
    for fold in folds:
        assert fold["train"]["target_interval_start_utc"].max() < fold["test_start_utc"]
        assert fold["test"]["target_interval_start_utc"].min() == fold["test_start_utc"]
        assert len(fold["test"]) == 7
    for previous, current in zip(folds, folds[1:]):
        assert previous["test_end_exclusive_utc"] == current["test_start_utc"]


def test_rolling_origin_supports_a_fixed_recent_training_window():
    frame = pd.DataFrame(
        {"target_interval_start_utc": pd.date_range("2025-01-01", periods=250, freq="D", tz="UTC")}
    )

    first_fold = rolling_origin_splits(
        frame,
        minimum_train_days=180,
        test_window_days=7,
        step_days=7,
        training_window_days=30,
        resolution_minutes=1440,
    )[0]

    assert len(first_fold["train"]) == 30
    assert first_fold["train"]["target_interval_start_utc"].min() == first_fold["test_start_utc"] - pd.Timedelta(days=30)


def test_rolling_origin_rejects_overlapping_test_windows():
    frame = pd.DataFrame(
        {"target_interval_start_utc": pd.date_range("2025-01-01", periods=250, freq="D", tz="UTC")}
    )

    try:
        rolling_origin_splits(
            frame,
            minimum_train_days=180,
            test_window_days=7,
            step_days=1,
            resolution_minutes=1440,
        )
    except ValueError as exc:
        assert "avoid scoring rows more than once" in str(exc)
    else:
        raise AssertionError("Expected overlapping windows to fail")


def test_weekly_lag_baseline_uses_only_lags_marked_present():
    frame = pd.DataFrame(
        {
            "price_lag_168h_eur_mwh": [100.0, 200.0, None],
            "price_lag_168h_eur_mwh_present": [True, False, True],
        }
    )

    predictions = _weekly_lag_predictions(frame)

    assert predictions.iloc[0] == 100.0
    assert pd.isna(predictions.iloc[1])
    assert pd.isna(predictions.iloc[2])


def test_weekly_lag_metrics_compare_methods_on_the_same_available_rows():
    scored = pd.DataFrame(
        {
            "bidding_zone": ["FR", "FR", "NL"],
            TARGET: [10.0, 20.0, 30.0],
            "model_prediction": [12.0, 22.0, 40.0],
            "seasonal_baseline_prediction": [15.0, 15.0, 25.0],
            "weekly_lag_baseline_prediction": [11.0, float("nan"), 28.0],
        }
    )

    metrics = _weekly_lag_comparison(scored)

    assert metrics["rows"] == 2
    assert metrics["coverage"] == 2 / 3
    assert metrics["model_on_available_rows"]["mae"] == 6.0
    assert metrics["weekly_lag_baseline"]["mae"] == 1.5
    assert set(metrics["by_zone"]) == {"FR", "NL"}
