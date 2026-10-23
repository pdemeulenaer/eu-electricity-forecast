"""Survey ENTSO-E history, units, and observed resolutions by zone and data item."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from eu_electricity_forecast.entsoe import (
    EntsoeClient,
    load_config,
    parse_datetime,
    parse_publication,
    query_parameters,
    resolve_token,
    utc_text,
)
from eu_electricity_forecast.ingest import DEFAULT_AREA_ITEMS, _chunks


def _query_document(client: EntsoeClient, config: dict, *, zone: str, item: str, start: datetime, end: datetime,
                    other_zone: str | None = None, raw_dir: Path | None = None) -> dict:
    params = query_parameters(config, zone, item, start, end, other_zone)
    payload = client.get(params)
    if raw_dir is not None:
        payload_hash = hashlib.sha256(payload).hexdigest()
        raw_dir.mkdir(parents=True, exist_ok=True)
        xml_path = raw_dir / f"{payload_hash}.xml"
        if not xml_path.exists():
            xml_path.write_bytes(payload)
        request_identity = json.dumps({"zone": zone, "other_zone": other_zone, "item": item,
            "start": utc_text(start), "end": utc_text(end), "payload_sha256": payload_hash}, sort_keys=True)
        request_hash = hashlib.sha256(request_identity.encode()).hexdigest()
        metadata = {"zone": zone, "other_zone": other_zone, "data_item": item, "request_parameters": params,
            "request_start_utc": utc_text(start), "request_end_utc": utc_text(end),
            "response_sha256": payload_hash, "response_path": xml_path.name}
        metadata_path = raw_dir / f"{request_hash}.json"
        if not metadata_path.exists():
            metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    document = parse_publication(payload, zone=f"{zone}->{other_zone}" if other_zone else zone, data_item=item)
    document["request_parameters"] = params
    return document


def summarize(zone: str, item: str, spec: dict, docs: list[dict], start: datetime, end: datetime) -> dict:
    all_rows = [row for doc in docs for row in doc["records"]]
    rows = [row for row in all_rows if start <= parse_datetime(row["timestamp_utc"]) < end]
    times = sorted({parse_datetime(row["timestamp_utc"]) for row in rows})
    resolution_counts = Counter(row["resolution_minutes"] for row in rows)
    unique_interval_resolutions = {(row["timestamp_utc"], row["resolution_minutes"]) for row in rows}
    resolution = resolution_counts.most_common(1)[0][0] if resolution_counts else None
    resolutions_by_year: dict[str, Counter] = {}
    for row in rows:
        year = parse_datetime(row["timestamp_utc"]).strftime("%Y")
        resolutions_by_year.setdefault(year, Counter())[row["resolution_minutes"]] += 1
    source_record_keys = [row.get("source_record_key") for row in rows]
    source_record_keys = [key for key in source_record_keys if key is not None]
    source_version_keys = [row.get("source_record_version_key") for row in rows]
    source_version_keys = [key for key in source_version_keys if key is not None]
    series_ids = sorted({row["source_series_id"] for row in rows if row.get("source_series_id")})
    classification_sequences = sorted({str(row["classification_sequence"]) for row in rows
                                       if row.get("classification_sequence") is not None})
    domain_pairs = sorted({(row.get("in_domain_eic"), row.get("out_domain_eic")) for row in rows
                           if row.get("in_domain_eic") or row.get("out_domain_eic")},
                          key=lambda pair: (pair[0] or "", pair[1] or ""))
    return {"zone": zone, "data_item": item, "description": spec.get("description"),
        "requested_start_utc": utc_text(start), "requested_end_utc": utc_text(end), "record_count": len(rows),
        "records_outside_requested_window": len(all_rows) - len(rows),
        "unique_intervals": len(times), "first_interval_utc": utc_text(times[0]) if times else None,
        "last_interval_utc": utc_text(times[-1]) if times else None,
        "unique_interval_resolution_pairs": len(unique_interval_resolutions),
        "duplicate_logical_record_count": len(source_record_keys) - len(set(source_record_keys)),
        "duplicate_exact_version_count": len(source_version_keys) - len(set(source_version_keys)),
        "source_series_count": len(series_ids), "source_series_ids_sample": series_ids[:10],
        "classification_sequences": classification_sequences,
        "source_domain_pairs": [{"in_domain_eic": first, "out_domain_eic": second} for first, second in domain_pairs],
        "expected_unit": spec.get("unit"),
        "observed_units": sorted({row["unit"] for row in rows if row.get("unit")}),
        "observed_source_unit_codes": sorted({row["source_unit_code"] for row in rows if row.get("source_unit_code")}),
        "observed_currency_codes": sorted({row["source_currency_code"] for row in rows if row.get("source_currency_code")}),
        "resolution_counts_minutes": dict(sorted(resolution_counts.items())),
        "resolution_counts_minutes_by_year": {
            year: dict(sorted(counts.items())) for year, counts in sorted(resolutions_by_year.items())
        },
        "dominant_resolution_minutes": resolution,
        "interval_coverage_status": "Not estimated: expected resolution is data-item-specific; inspect interval counts and resolution by year.",
        "documents": len(docs), "no_data_responses": sum(not doc["records"] for doc in docs),
        "request_parameters": docs[0].get("request_parameters") if docs else None,
        "timing_note": "Document createdDateTime is retained on records but may reflect later revisions, not first publication. A historical backfill cannot establish initial availability; estimate operational delay from first-seen timestamps during recurring polls.",
        "publication_expectation": spec.get("publication_expectation"),
        "production_types": sorted({row["production_type"] for row in rows if row.get("production_type")}),
        "document_ids_sample": sorted({doc["document_id"] for doc in docs if doc.get("document_id")})[:10]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Survey ENTSO-E history, units, and observed resolutions by zone and data item.")
    parser.add_argument("--start", required=True, help="Inclusive ISO-8601 timestamp")
    parser.add_argument("--end", required=True, help="Exclusive ISO-8601 timestamp")
    parser.add_argument("--output", default="reports/entsoe-coverage.json")
    parser.add_argument("--data-items", default=",".join(DEFAULT_AREA_ITEMS))
    parser.add_argument("--include-flows", action="store_true", help="Survey both directions for each configured border")
    parser.add_argument("--raw-dir", help="Optionally save exact XML responses and token-free request metadata")
    parser.add_argument("--secret-scope"); parser.add_argument("--secret-key")
    args = parser.parse_args(); start, end = parse_datetime(args.start), parse_datetime(args.end)
    if start >= end:
        parser.error("--start must be before --end")
    config = load_config()
    raw_dir = Path(args.raw_dir) if args.raw_dir else None
    items = tuple(value.strip() for value in args.data_items.split(",") if value.strip())
    if set(items) - set(config["data_items"]) or "physical_flows" in items:
        parser.error("Use configured area-based items; use --include-flows for border queries")
    if args.include_flows and not config.get("physical_flow_borders"):
        parser.error("Configure physical_flow_borders before requesting physical flows")
    client = EntsoeClient(resolve_token(args.secret_scope, args.secret_key), endpoint=config["api_endpoint"],
                          requests_per_minute=config["requests_per_minute"])
    results = []
    for zone in config["zones"]:
        for item in items:
            docs = [_query_document(client, config, zone=zone, item=item, start=left, end=right, raw_dir=raw_dir)
                    for left, right in _chunks(start, end)]
            result = summarize(zone, item, config["data_items"][item], docs, start, end)
            result["eic_code"] = config["zones"][zone]["eic_code"]
            results.append(result)
    if args.include_flows:
        for first, second in config["physical_flow_borders"]:
            for origin, destination in ((first, second), (second, first)):
                docs = [_query_document(client, config, zone=origin, item="physical_flows", start=left, end=right,
                                        other_zone=destination, raw_dir=raw_dir)
                        for left, right in _chunks(start, end)]
                results.append(summarize(f"{origin}->{destination}", "physical_flows", config["data_items"]["physical_flows"], docs, start, end))
    report = {"surveyed_at_utc": utc_text(datetime.now(UTC)), "endpoint": config["api_endpoint"],
              "forecast": config["forecast"], "results": results}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "query_groups": len(results), "zones": list(config["zones"])}))

if __name__ == "__main__":
    main()
