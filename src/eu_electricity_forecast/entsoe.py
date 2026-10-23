"""ENTSO-E Transparency Platform XML client and publication parser."""
from __future__ import annotations

import hashlib
import importlib.resources
import os
import random
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import requests

PARSER_VERSION = "1"

class EntsoeError(RuntimeError):
    """Source error safe to log; authenticated request URLs are omitted."""


def load_config() -> dict[str, Any]:
    import json
    from pathlib import Path
    bundled = importlib.resources.files("eu_electricity_forecast").joinpath("entsoe.json")
    if bundled.is_file():
        return json.loads(bundled.read_text(encoding="utf-8"))
    return json.loads((Path(__file__).parents[2] / "config" / "entsoe.json").read_text(encoding="utf-8"))


def parse_datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value}")
    return result.astimezone(UTC)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ET.Element, name: str) -> str | None:
    for child in node.iter():
        if _name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def _duration(value: str) -> timedelta:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        raise EntsoeError(f"Unsupported ENTSO-E resolution {value!r}")
    hours, minutes, seconds = (int(n or 0) for n in match.groups())
    duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    if duration.total_seconds() <= 0:
        raise EntsoeError(f"Invalid ENTSO-E resolution {value!r}")
    return duration


def _display_unit(code: str | None) -> str | None:
    if code is None:
        return None
    return {"MAW": "MW", "MWH": "MWh"}.get(code, code)


def parse_publication(payload: bytes, *, zone: str, data_item: str, fetched_at: datetime | None = None) -> dict[str, Any]:
    """Parse interval points and retain source document/revision metadata."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise EntsoeError("ENTSO-E response was not valid XML") from exc
    kind, document_id = _name(root.tag), _text(root, "mRID")
    created, revision = _text(root, "createdDateTime"), _text(root, "revisionNumber")
    published = utc_text(parse_datetime(created)) if created else None
    seen = utc_text(fetched_at or datetime.now(UTC))
    if "Acknowledgement" in kind:
        reasons = [n.text.strip() for n in root.iter() if _name(n.tag) == "text" and n.text]
        return {"document_type": kind, "document_id": document_id, "revision": revision,
                "published_at_utc": published, "first_seen_at_utc": seen, "records": [],
                "no_data_reason": "; ".join(reasons) or "No matching data"}
    records = []
    for series in (node for node in root.iter() if _name(node.tag) == "TimeSeries"):
        series_id = _text(series, "mRID")
        business_type = _text(series, "businessType")
        process_type = _text(series, "process.processType")
        production = _text(series, "psrType")
        in_domain = _text(series, "in_Domain.mRID")
        out_domain = _text(series, "out_Domain.mRID")
        in_bidding_zone = _text(series, "inBiddingZone_Domain.mRID")
        out_bidding_zone = _text(series, "outBiddingZone_Domain.mRID")
        market_agreement_id = _text(series, "marketAgreement.mRID")
        curve_type = _text(series, "curveType")
        auction_type = _text(series, "auction.type")
        auction_category = _text(series, "auction.category")
        contract_type = _text(series, "contract_MarketAgreement.type")
        classification_sequence = _text(series, "classificationSequence_AttributeInstanceComponent.position")
        currency = _text(series, "currency_Unit.name") or _text(series, "currency_Measure_Unit.name")
        price_unit = _text(series, "price_Measure_Unit.name")
        quantity_unit = _text(series, "quantity_Measure_Unit.name")
        source_unit_code = price_unit or quantity_unit
        if currency and price_unit:
            unit = f"{currency}/{_display_unit(price_unit)}"
        else:
            unit = _display_unit(source_unit_code)
        for period in (node for node in series if _name(node.tag) == "Period"):
            interval = next((node for node in period if _name(node.tag) == "timeInterval"), None)
            if interval is None:
                continue
            begin, finish, resolution = _text(interval, "start"), _text(interval, "end"), _text(period, "resolution")
            if not begin or not finish or not resolution:
                continue
            start, end, step = parse_datetime(begin), parse_datetime(finish), _duration(resolution)
            if end <= start:
                raise EntsoeError("ENTSO-E returned an invalid time interval")
            for point in (node for node in period if _name(node.tag) == "Point"):
                pos, amount = _text(point, "position"), _text(point, "price.amount") or _text(point, "quantity")
                if not pos or not amount:
                    continue
                timestamp = start + step * (int(pos) - 1)
                if timestamp >= end:
                    continue
                logical_identity = "|".join((zone, data_item, timestamp.isoformat(), str(int(step.total_seconds() // 60)),
                    business_type or "", process_type or "", production or "", in_domain or "", out_domain or "",
                    in_bidding_zone or "", out_bidding_zone or "", market_agreement_id or "", curve_type or "",
                    source_unit_code or "", currency or "", auction_type or "", auction_category or "",
                    contract_type or "", classification_sequence or ""))
                record_key = hashlib.sha256(logical_identity.encode()).hexdigest()
                version_identity = "|".join((record_key, document_id or "", revision or "", amount))
                records.append({"timestamp_utc": utc_text(timestamp), "interval_end_utc": utc_text(min(timestamp + step, end)),
                    "bidding_zone": zone, "data_item": data_item, "value": float(amount), "unit": unit,
                    "source_unit_code": source_unit_code, "source_currency_code": currency,
                    "resolution_minutes": int(step.total_seconds() // 60), "source_document_id": document_id,
                    "source_revision": int(revision) if revision and revision.isdigit() else revision,
                    "parser_version": PARSER_VERSION,
                    "published_at_utc": published, "first_seen_at_utc": seen, "ingested_at_utc": seen,
                    "production_type": production, "business_type": business_type, "process_type": process_type,
                    "curve_type": curve_type, "market_agreement_id": market_agreement_id,
                    "auction_type": auction_type, "auction_category": auction_category,
                    "contract_type": contract_type,
                    "classification_sequence": int(classification_sequence) if classification_sequence and classification_sequence.isdigit() else classification_sequence,
                    "source_series_id": series_id, "in_domain_eic": in_domain, "out_domain_eic": out_domain,
                    "in_bidding_zone_eic": in_bidding_zone, "out_bidding_zone_eic": out_bidding_zone,
                    "source_record_key": record_key,
                    "source_record_version_key": hashlib.sha256(version_identity.encode()).hexdigest()})
    return {"document_type": kind, "document_id": document_id, "revision": revision,
            "published_at_utc": published, "first_seen_at_utc": seen, "records": records,
            "no_data_reason": None if records else "No interval points in response"}

class EntsoeClient:
    def __init__(self, token: str, *, endpoint: str, requests_per_minute: int = 30, attempts: int = 4):
        if not token:
            raise EntsoeError("Set ENTSOE_API_TOKEN or configure the Databricks token secret")
        if not 1 <= requests_per_minute <= 400:
            raise ValueError("requests_per_minute must be from 1 to 400")
        self.token, self.endpoint, self.attempts = token, endpoint, attempts
        self.spacing, self.last, self.lock = 60 / requests_per_minute, 0.0, threading.Lock()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "eu-electricity-forecast/0.1"

    def get(self, parameters: dict[str, str]) -> bytes:
        query = {**parameters, "securityToken": self.token}
        for attempt in range(self.attempts):
            with self.lock:
                time.sleep(max(0.0, self.spacing - (time.monotonic() - self.last)))
                self.last = time.monotonic()
            try:
                response = self.session.get(self.endpoint, params=query, timeout=300)
            except requests.RequestException:
                if attempt + 1 == self.attempts:
                    raise EntsoeError(f"ENTSO-E transport failed after {self.attempts} attempts") from None
                base_delay = min(2 ** attempt, 30)
                time.sleep(random.uniform(base_delay * 0.5, base_delay * 1.5))
                continue
            if response.status_code == 200:
                return response.content
            if response.status_code in {401, 403}:
                raise EntsoeError(f"ENTSO-E rejected credentials or access (HTTP {response.status_code})")
            if response.status_code not in {429, 500, 502, 503, 504, 599} or attempt + 1 == self.attempts:
                raise EntsoeError(f"ENTSO-E request failed (HTTP {response.status_code}); parameters={parameters}")
            delay = 2 ** attempt
            retry = response.headers.get("Retry-After")
            if retry:
                try:
                    delay = max(delay, int(retry))
                except ValueError:
                    try:
                        delay = max(delay, int((parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds()))
                    except (ValueError, TypeError, OverflowError):
                        pass
            bounded_delay = min(max(delay, 1), 600)
            time.sleep(min(random.uniform(bounded_delay * 0.8, bounded_delay * 1.2), 600))
        raise EntsoeError("ENTSO-E request did not return a response")


def query_parameters(config: dict[str, Any], zone: str, item: str, start: datetime, end: datetime,
                     other_zone: str | None = None) -> dict[str, str]:
    spec, area = config["data_items"][item], config["zones"][zone]["eic_code"]
    params = {"documentType": spec["documentType"], spec["domain_parameter"]: area,
              "periodStart": start.astimezone(UTC).strftime("%Y%m%d%H%M"),
              "periodEnd": end.astimezone(UTC).strftime("%Y%m%d%H%M")}
    if other_zone:
        params[spec["out_domain_parameter"]] = config["zones"][other_zone]["eic_code"]
    for key in spec.get("same_domain_parameters", []):
        params[key] = area
    if spec.get("processType"):
        params["processType"] = spec["processType"]
    return params


def resolve_token(scope: str | None = None, key: str | None = None) -> str:
    token = os.getenv("ENTSOE_API_TOKEN")
    if token:
        return token
    if scope and key:
        try:
            from databricks.sdk.runtime import dbutils
            token = dbutils.secrets.get(scope=scope, key=key)
        except Exception as exc:
            raise EntsoeError(f"Could not read configured Databricks secret {scope}/{key}") from exc
        if token:
            return token
    raise EntsoeError("Set ENTSOE_API_TOKEN or supply Databricks secret scope and key")
