from eu_electricity_forecast.training import _log_code_provenance


def test_experiment_provenance_records_build_details(monkeypatch):
    recorded = {}

    class FakeMlflow:
        @staticmethod
        def set_tags(tags):
            recorded.update(tags)

    monkeypatch.setenv("GIT_COMMIT", "abc123")
    monkeypatch.setenv("WHEEL_SHA256", "checksum")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "qa")
    _log_code_provenance(FakeMlflow)

    assert recorded["code.package_version"] != "unknown"
    assert recorded["code.git_commit"] == "abc123"
    assert recorded["code.wheel_sha256"] == "checksum"
    assert recorded["code.environment"] == "qa"


def test_explicit_code_revision_takes_precedence(monkeypatch):
    recorded = {}

    class FakeMlflow:
        @staticmethod
        def set_tags(tags):
            recorded.update(tags)

    monkeypatch.setenv("GIT_COMMIT", "environment")
    _log_code_provenance(FakeMlflow, "explicit")
    assert recorded["code.git_commit"] == "explicit"


def test_silver_cli_separates_input_and_output_catalogs(monkeypatch):
    import sys

    from eu_electricity_forecast import silver

    captured = {}
    monkeypatch.setattr(silver, "run_silver", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["silver", "--catalog", "qa", "--input-catalog", "prod", "--bronze-schema", "bronze", "--silver-schema", "silver"])
    silver.main()
    assert captured["catalog"] == "qa"
    assert captured["input_catalog"] == "prod"


def test_gold_cli_separates_input_and_output_catalogs(monkeypatch):
    import sys

    from eu_electricity_forecast import gold
    from eu_electricity_forecast import entsoe

    captured = []
    monkeypatch.setattr(entsoe, "load_config", lambda: {})
    monkeypatch.setattr(gold, "build_day_ahead_features", lambda **kwargs: captured.append(kwargs))
    monkeypatch.setattr(gold, "build_day_ahead_price_labels", lambda **kwargs: captured.append(kwargs))
    monkeypatch.setattr(gold, "build_day_ahead_training_view", lambda **kwargs: captured.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["gold", "--catalog", "qa", "--input-catalog", "prod", "--silver-schema", "silver", "--gold-schema", "gold"])
    gold.main()
    assert all(call["catalog"] == "qa" for call in captured)
    assert captured[0]["input_catalog"] == "prod"
    assert captured[1]["input_catalog"] == "prod"
