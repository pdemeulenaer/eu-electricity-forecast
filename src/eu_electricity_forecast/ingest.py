"""Backfill or poll ENTSO-E, landing immutable XML and newline-delimited JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from eu_electricity_forecast.entsoe import (
    PARSER_VERSION,
    EntsoeClient,
    EntsoeError,
    load_config,
    parse_datetime,
    parse_publication,
    query_parameters,
    resolve_token,
    utc_text,
)

DEFAULT_AREA_ITEMS = (
    "day_ahead_prices",
    "actual_load",
    "day_ahead_load_forecast",
    "actual_generation_per_type",
    "day_ahead_generation",
    "day_ahead_wind_solar_forecast",
)
DEFAULT_ITEMS = (*DEFAULT_AREA_ITEMS, "physical_flows")


def _atomic_create(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            return False
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _chunks(start: datetime, end: datetime):
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=364), end)
        yield cursor, stop
        cursor = stop


def _request_fingerprint(zone: str, item: str, params: dict[str, str], other_zone: str | None = None) -> str:
    identity = json.dumps({"zone": zone, "other_zone": other_zone, "data_item": item,
                           "request_parameters": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def _is_backfill_complete(landing: Path, fingerprint: str) -> bool:
    return (landing / "ledger" / f"{fingerprint}.json").is_file()


def _is_query_limit_rejection(reason: str | None) -> bool:
    normalized = (reason or "").lower()
    return any(
        phrase in normalized
        for phrase in (
            "more than 100 matching documents",
            "more than 200 matching documents",
            "date range exceeds permitted limit",
            "requested date range exceeds",
        )
    )


def fetch_and_land(client: EntsoeClient, config: dict[str, Any], landing: Path, *, zone: str, item: str,
                   start: datetime, end: datetime, run_id: str, other_zone: str | None = None) -> dict[str, Any]:
    params = query_parameters(config, zone, item, start, end, other_zone)
    raw = client.get(params); raw_hash = hashlib.sha256(raw).hexdigest()
    xml_rel = Path("raw") / raw_hash[:2] / f"{raw_hash}.xml"
    _atomic_create(landing / xml_rel, raw)
    fetched = datetime.now(UTC)
    parsed = parse_publication(raw, zone=f"{zone}->{other_zone}" if other_zone else zone, data_item=item, fetched_at=fetched)
    request_fingerprint = _request_fingerprint(zone, item, params, other_zone)
    request_identity = json.dumps({"request_fingerprint": request_fingerprint, "raw_xml_sha256": raw_hash},
                                  sort_keys=True, separators=(",", ":"))
    request_id = hashlib.sha256(request_identity.encode()).hexdigest()
    if "Acknowledgement" in parsed["document_type"] and _is_query_limit_rejection(parsed["no_data_reason"]):
        rejection = {"zone": zone, "other_zone": other_zone, "data_item": item,
                     "request_start_utc": utc_text(start), "request_end_utc": utc_text(end),
                     "fetched_at_utc": utc_text(fetched), "http_status": 200,
                     "response_type": parsed["document_type"], "status": "rejected_query_limit",
                     "no_data_reason": parsed["no_data_reason"], "raw_xml_sha256": raw_hash,
                     "raw_xml_path": str(xml_rel), "parser_version": PARSER_VERSION, "run_id": run_id}
        _atomic_create(landing / "metadata" / f"{request_id}.json",
                       (json.dumps(rejection, sort_keys=True, indent=2) + "\n").encode())
        raise EntsoeError(f"query limit rejection: {parsed['no_data_reason']}")
    rows = []
    for record in parsed["records"]:
        record.update({"run_id": run_id, "source_xml_sha256": raw_hash, "source_xml_path": str(xml_rel),
                       "request_start_utc": utc_text(start), "request_end_utc": utc_text(end),
                       "from_zone": zone if other_zone else None, "to_zone": other_zone})
        rows.append(record)
    data_rel = Path("data") / zone / item / f"{request_id}.json"
    if rows:
        data = b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows)
        _atomic_create(landing / data_rel, data)
    status = {"zone": zone, "other_zone": other_zone, "data_item": item, "request_parameters": params,
              "request_start_utc": utc_text(start), "request_end_utc": utc_text(end), "fetched_at_utc": utc_text(fetched),
              "http_status": 200, "document_type": parsed["document_type"], "document_id": parsed["document_id"],
              "revision": parsed["revision"], "published_at_utc": parsed["published_at_utc"],
              "first_seen_at_utc": parsed["first_seen_at_utc"], "record_count": len(rows),
              "resolution_minutes": sorted({row["resolution_minutes"] for row in rows}),
              "no_data_reason": parsed["no_data_reason"], "raw_xml_sha256": raw_hash, "raw_xml_path": str(xml_rel),
              "data_path": str(data_rel) if rows else None, "parser_version": PARSER_VERSION, "run_id": run_id}
    meta_rel = Path("metadata") / f"{request_id}.json"
    if not (landing / meta_rel).exists():
        _atomic_create(landing / meta_rel, (json.dumps(status, sort_keys=True, indent=2) + "\n").encode())
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("poll", "backfill"), default="poll")
    parser.add_argument("--landing-dir", default="data/entsoe_landing")
    parser.add_argument("--start", help="Inclusive ISO-8601 timestamp; required for backfill")
    parser.add_argument("--end", help="Exclusive ISO-8601 timestamp; defaults to now")
    parser.add_argument("--overlap-hours", type=int, default=6)
    parser.add_argument("--requery-completed", action="store_true",
                        help="Re-fetch completed backfill windows to capture source revisions")
    parser.add_argument("--data-items", default=",".join(DEFAULT_ITEMS))
    parser.add_argument("--secret-scope"); parser.add_argument("--secret-key")
    args = parser.parse_args(); config = load_config()
    items = tuple(value.strip() for value in args.data_items.split(",") if value.strip())
    invalid = set(items) - set(config["data_items"])
    if invalid:
        parser.error(f"Unknown data items: {', '.join(sorted(invalid))}")
    if "physical_flows" in items and not config.get("physical_flow_borders"):
        parser.error("Configure physical_flow_borders before requesting physical flows")
    end = parse_datetime(args.end) if args.end else datetime.now(UTC)
    if args.mode == "backfill" and not args.start:
        parser.error("--start is required in backfill mode")
    if args.mode == "poll" and args.overlap_hours < 1:
        parser.error("--overlap-hours must be positive")
    start = parse_datetime(args.start) if args.start else end - timedelta(hours=args.overlap_hours)
    if start >= end:
        parser.error("--start must be before --end")
    client = EntsoeClient(resolve_token(args.secret_scope, args.secret_key), endpoint=config["api_endpoint"],
                          requests_per_minute=config["requests_per_minute"])
    run_id = str(uuid.uuid4()); failures = 0; landing = Path(args.landing_dir)
    requests = [(zone, item, None) for zone in config["zones"] for item in items if item != "physical_flows"]
    if "physical_flows" in items:
        for first, second in config["physical_flow_borders"]:
            requests.extend(((first, "physical_flows", second), (second, "physical_flows", first)))
    for zone, item, other_zone in requests:
        for left, right in _chunks(start, end):
            pending = [(left, right)]
            while pending:
                query_start, query_end = pending.pop(0)
                params = query_parameters(config, zone, item, query_start, query_end, other_zone)
                fingerprint = _request_fingerprint(zone, item, params, other_zone)
                if args.mode == "backfill" and not args.requery_completed and _is_backfill_complete(landing, fingerprint):
                    print(json.dumps({"zone": zone, "other_zone": other_zone, "data_item": item,
                                      "status": "already_complete", "run_id": run_id}))
                    continue
                try:
                    result = fetch_and_land(client, config, landing, zone=zone, item=item, start=query_start,
                                            end=query_end, run_id=run_id, other_zone=other_zone)
                    if args.mode == "backfill":
                        marker = {"zone": zone, "other_zone": other_zone, "data_item": item,
                                  "request_parameters": params, "request_start_utc": utc_text(query_start),
                                  "request_end_utc": utc_text(query_end), "completed_at_utc": utc_text(datetime.now(UTC)),
                                  "raw_xml_sha256": result["raw_xml_sha256"], "run_id": run_id}
                        marker_path = landing / "ledger" / f"{fingerprint}.json"
                        _atomic_create(marker_path, (json.dumps(marker, sort_keys=True, indent=2) + "\n").encode())
                    print(json.dumps({"zone": zone, "other_zone": other_zone, "data_item": item,
                                      "record_count": result["record_count"],
                                      "resolution_minutes": result["resolution_minutes"],
                                      "no_data_reason": result["no_data_reason"], "run_id": run_id}))
                except EntsoeError as exc:
                    if _is_query_limit_rejection(str(exc)) and query_end - query_start > timedelta(hours=1):
                        midpoint = query_start + (query_end - query_start) / 2
                        pending[0:0] = [(query_start, midpoint), (midpoint, query_end)]
                        print(json.dumps({"zone": zone, "other_zone": other_zone, "data_item": item,
                                          "status": "split_query_limit", "request_start_utc": utc_text(query_start),
                                          "request_end_utc": utc_text(query_end), "run_id": run_id}))
                        continue
                    failures += 1
                    print(f"request failed zone={zone} other_zone={other_zone} item={item}: {exc}", file=sys.stderr)
    if failures:
        raise SystemExit(f"{failures} source request(s) failed")

if __name__ == "__main__":
    main()
