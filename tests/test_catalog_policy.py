import pytest

from eu_electricity_forecast.catalog_policy import validate_source_catalog


@pytest.mark.parametrize(
    ("environment", "output", "source"),
    [("dev", "dev", "dev"), ("dev", "dev", "prod"), ("qa", "qa", "qa"),
     ("qa", "qa", "prod"), ("prod", "prod", "prod")],
)
def test_allowed_catalogs(environment, output, source):
    validate_source_catalog(environment=environment, output_catalog=output, input_catalog=source, production_catalog="prod")


@pytest.mark.parametrize(
    ("environment", "output", "source"),
    [("dev", "prod", "prod"), ("qa", "prod", "prod"), ("qa", "qa", "dev"),
     ("prod", "prod", "qa"), ("prod", "qa", "prod")],
)
def test_disallowed_catalogs(environment, output, source):
    with pytest.raises(ValueError):
        validate_source_catalog(environment=environment, output_catalog=output, input_catalog=source, production_catalog="prod")


def test_production_schema_grants_do_not_give_dev_or_qa_write_privileges():
    from pathlib import Path

    import yaml

    root = Path(__file__).parents[1]
    config = yaml.safe_load((root / "resources/environment_access.schemas.yml").read_text())
    schemas = config["targets"]["prod"]["resources"]["schemas"]
    for schema in schemas.values():
        for grant in schema["grants"]:
            if grant["principal"] in {"${var.dev_user}", "${var.qa_principal}"}:
                assert set(grant["privileges"]) <= {"USE_SCHEMA", "SELECT", "READ_VOLUME", "EXECUTE"}


def test_qa_schema_grants_give_developer_only_read_access():
    from pathlib import Path

    import yaml

    root = Path(__file__).parents[1]
    config = yaml.safe_load((root / "resources/environment_access.schemas.yml").read_text())
    schemas = config["targets"]["qa"]["resources"]["schemas"]
    for schema in schemas.values():
        reader = next(grant for grant in schema["grants"] if grant["principal"] == "${var.dev_user}")
        assert set(reader["privileges"]) <= {"USE_SCHEMA", "SELECT", "READ_VOLUME", "EXECUTE"}
