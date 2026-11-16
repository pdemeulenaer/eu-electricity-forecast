"""Validate identifiers and catalog grants in the provisioning notebook."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

NOTEBOOK = Path(__file__).parents[1] / "src/notebooks/provision_catalog.py"


def _run(catalog, writer="qa-principal", readers=(), current=None):
    queries = []
    values = {"catalog": catalog, "writer": writer, "readers_json": json.dumps(readers)}
    widgets = SimpleNamespace(text=lambda *_: None, get=values.__getitem__)
    def sql(query):
        if query == "SELECT current_user()":
            return SimpleNamespace(first=lambda: [current if current is not None else writer])
        queries.append(query)

    runpy.run_path(
        str(NOTEBOOK),
        init_globals={"dbutils": SimpleNamespace(widgets=widgets), "spark": SimpleNamespace(sql=sql)},
    )
    return queries


def test_provision_catalog_uses_idempotent_create_and_catalog_usage_grants():
    assert _run("eu_electricity_forecast_prod", "prod", ["qa", "user@example.com"]) == [
        "CREATE CATALOG IF NOT EXISTS `eu_electricity_forecast_prod`",
        "GRANT USE CATALOG ON CATALOG `eu_electricity_forecast_prod` TO `prod`",
        "GRANT USE CATALOG ON CATALOG `eu_electricity_forecast_prod` TO `qa`",
        "GRANT USE CATALOG ON CATALOG `eu_electricity_forecast_prod` TO `user@example.com`",
    ]


@pytest.mark.parametrize("catalog", ["", "bad-name", "a`; DROP CATALOG x", "a.b", "UPPER", "1first"])
def test_provision_catalog_rejects_invalid_names(catalog):
    with pytest.raises(ValueError):
        _run(catalog)


@pytest.mark.parametrize("writer,readers", [("", []), ("prod`", []), ("prod", ["bad; name"]), ("prod", ["prod"])])
def test_provision_catalog_rejects_invalid_or_duplicate_principals(writer, readers):
    with pytest.raises(ValueError):
        _run("catalog", writer, readers)


def test_provision_catalog_rejects_deployment_as_another_principal():
    with pytest.raises(ValueError, match="configured service principal"):
        _run("catalog", writer="qa", current="other-user@example.com")
