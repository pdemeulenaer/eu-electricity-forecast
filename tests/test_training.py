import pandas as pd

from eu_electricity_forecast.training import PREDICTORS, _validate_and_prepare, chronological_split


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
