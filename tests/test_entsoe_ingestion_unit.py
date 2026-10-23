import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from eu_electricity_forecast import ingest
from eu_electricity_forecast.entsoe import parse_publication
from eu_electricity_forecast.ingest import fetch_and_land

XML = b"""<Publication_MarketDocument>
  <mRID>document-1</mRID><revisionNumber>1</revisionNumber>
  <createdDateTime>2026-10-01T08:00:00Z</createdDateTime>
  <TimeSeries><mRID>series-1</mRID><businessType>A62</businessType>
    <in_Domain.mRID>10Y1001A1001A82H</in_Domain.mRID><out_Domain.mRID>10Y1001A1001A82H</out_Domain.mRID>
    <auction.type>A01</auction.type><auction.category>A01</auction.category>
    <contract_MarketAgreement.type>A01</contract_MarketAgreement.type>
    <classificationSequence_AttributeInstanceComponent.position>1</classificationSequence_AttributeInstanceComponent.position>
    <currency_Unit.name>EUR</currency_Unit.name><price_Measure_Unit.name>MWH</price_Measure_Unit.name>
    <curveType>A01</curveType><Period><timeInterval><start>2026-10-01T00:00Z</start><end>2026-10-01T00:30Z</end></timeInterval>
      <resolution>PT15M</resolution><Point><position>1</position><price.amount>72.5</price.amount></Point></Period>
  </TimeSeries>
  <TimeSeries><mRID>series-2</mRID><businessType>A62</businessType>
    <in_Domain.mRID>10Y1001A1001A82H</in_Domain.mRID><out_Domain.mRID>10Y1001A1001A82H</out_Domain.mRID>
    <auction.type>A01</auction.type><auction.category>A01</auction.category>
    <contract_MarketAgreement.type>A01</contract_MarketAgreement.type>
    <classificationSequence_AttributeInstanceComponent.position>1</classificationSequence_AttributeInstanceComponent.position>
    <currency_Unit.name>EUR</currency_Unit.name><price_Measure_Unit.name>MWH</price_Measure_Unit.name>
    <curveType>A01</curveType><Period><timeInterval><start>2026-10-01T00:00Z</start><end>2026-10-01T00:30Z</end></timeInterval>
      <resolution>PT15M</resolution><Point><position>1</position><price.amount>72.5</price.amount></Point></Period>
  </TimeSeries>
  <TimeSeries><mRID>series-3</mRID><businessType>A62</businessType>
    <in_Domain.mRID>10Y1001A1001A82H</in_Domain.mRID><out_Domain.mRID>10Y1001A1001A82H</out_Domain.mRID>
    <auction.type>A01</auction.type><auction.category>A01</auction.category>
    <contract_MarketAgreement.type>A01</contract_MarketAgreement.type>
    <classificationSequence_AttributeInstanceComponent.position>2</classificationSequence_AttributeInstanceComponent.position>
    <currency_Unit.name>EUR</currency_Unit.name><price_Measure_Unit.name>MWH</price_Measure_Unit.name>
    <curveType>A01</curveType><Period><timeInterval><start>2026-10-01T00:00Z</start><end>2026-10-01T00:30Z</end></timeInterval>
      <resolution>PT15M</resolution><Point><position>1</position><price.amount>80</price.amount></Point></Period>
  </TimeSeries>
</Publication_MarketDocument>"""


def test_series_identity_keeps_auction_sequences_and_collapses_exact_copies():
    parsed = parse_publication(XML, zone="DE_LU", data_item="day_ahead_prices")
    first, duplicate, other_auction = parsed["records"]

    assert first["source_series_id"] != duplicate["source_series_id"]
    assert first["classification_sequence"] == 1
    assert first["source_record_key"] == duplicate["source_record_key"]
    assert first["source_record_version_key"] == duplicate["source_record_version_key"]
    assert other_auction["classification_sequence"] == 2
    assert other_auction["source_record_key"] != first["source_record_key"]


def test_repeated_identical_fetch_creates_one_immutable_observation_file(tmp_path: Path):
    from eu_electricity_forecast.entsoe import load_config

    class FakeClient:
        def get(self, parameters):
            return XML

    config = load_config()
    kwargs = {
        "client": FakeClient(),
        "config": config,
        "landing": tmp_path,
        "zone": "DE_LU",
        "item": "day_ahead_prices",
        "start": datetime(2026, 10, 1, tzinfo=UTC),
        "end": datetime(2026, 10, 2, tzinfo=UTC),
    }
    fetch_and_land(**kwargs, run_id="run-1")
    fetch_and_land(**kwargs, run_id="run-2")

    assert len(list((tmp_path / "data").rglob("*.json"))) == 1
    assert len(list((tmp_path / "metadata").glob("*.json"))) == 1
    assert len(list((tmp_path / "raw").rglob("*.xml"))) == 1


def test_backfill_skips_completed_windows(tmp_path: Path):
    config = ingest.load_config()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    for zone in config["zones"]:
        params = ingest.query_parameters(config, zone, "day_ahead_prices", start, end)
        fingerprint = ingest._request_fingerprint(zone, "day_ahead_prices", params)
        (ledger / f"{fingerprint}.json").write_text(json.dumps({"complete": True}), encoding="utf-8")

    class FakeClient:
        instance = None

        def __init__(self, *args, **kwargs):
            self.get_calls = 0
            type(self).instance = self

        def get(self, parameters):
            self.get_calls += 1
            raise AssertionError("completed backfill request was unexpectedly repeated")

    argv = ["entsoe-ingest", "--mode", "backfill", "--start", "2026-10-01T00:00:00Z",
            "--end", "2026-10-02T00:00:00Z", "--data-items", "day_ahead_prices",
            "--landing-dir", str(tmp_path)]
    with patch("sys.argv", argv), patch.object(ingest, "load_config", return_value=config), \
            patch.object(ingest, "resolve_token", return_value="test-token"), \
            patch.object(ingest, "EntsoeClient", FakeClient):
        ingest.main()
    assert FakeClient.instance.get_calls == 0


def test_query_limit_rejections_are_recognized_for_adaptive_splitting():
    assert ingest._is_query_limit_rejection("More than 100 matching documents")
    assert ingest._is_query_limit_rejection("Requested date range exceeds permitted limit of one year")
    assert not ingest._is_query_limit_rejection("No matching data")


def test_backfill_splits_rejected_ranges_and_marks_only_successful_windows(tmp_path: Path):
    config = ingest.load_config()
    config["zones"] = {"DE_LU": config["zones"]["DE_LU"]}
    config["physical_flow_borders"] = []
    acknowledgement = (
        b"<Acknowledgement_MarketDocument><Reason><text>More than 100 matching documents</text>"
        b"</Reason></Acknowledgement_MarketDocument>"
    )

    class FakeClient:
        instance = None

        def __init__(self, *args, **kwargs):
            self.get_calls = []
            type(self).instance = self

        def get(self, parameters):
            self.get_calls.append(parameters)
            start = datetime.strptime(parameters["periodStart"], "%Y%m%d%H%M").replace(tzinfo=UTC)
            end = datetime.strptime(parameters["periodEnd"], "%Y%m%d%H%M").replace(tzinfo=UTC)
            return acknowledgement if (end - start).total_seconds() > 12 * 3600 else XML

    argv = ["entsoe-ingest", "--mode", "backfill", "--start", "2026-10-01T00:00:00Z",
            "--end", "2026-10-02T00:00:00Z", "--data-items", "day_ahead_prices",
            "--landing-dir", str(tmp_path)]
    with patch("sys.argv", argv), patch.object(ingest, "load_config", return_value=config), \
            patch.object(ingest, "resolve_token", return_value="test-token"), \
            patch.object(ingest, "EntsoeClient", FakeClient):
        ingest.main()

    assert len(FakeClient.instance.get_calls) == 3
    assert len(list((tmp_path / "ledger").glob("*.json"))) == 2
    metadata = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / "metadata").glob("*.json")]
    assert sum(item.get("status") == "rejected_query_limit" for item in metadata) == 1
