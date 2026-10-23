from types import SimpleNamespace
from unittest.mock import patch

from eu_electricity_forecast.entsoe import EntsoeClient


def test_retries_http_599_then_returns_success():
    unavailable = SimpleNamespace(status_code=599, headers={}, content=b"")
    success = SimpleNamespace(status_code=200, headers={}, content=b"<response/>")
    client = EntsoeClient("test-token", endpoint="https://example.invalid/api",
                          requests_per_minute=400, attempts=2)

    with patch.object(client.session, "get", side_effect=[unavailable, success]) as get, \
            patch("eu_electricity_forecast.entsoe.time.sleep"):
        assert client.get({"documentType": "A44"}) == b"<response/>"

    assert get.call_count == 2
