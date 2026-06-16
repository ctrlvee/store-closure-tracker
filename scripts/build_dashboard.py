from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DATA_DIR = ROOT / "docs" / "data"
CLOSURES_CSV = DATA_DIR / "closures.csv"
OUT_JSON = DOCS_DATA_DIR / "closures.json"


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def pick_date_key(row: dict) -> str:
    dt = parse_iso(row.get("published_utc", "")) or parse_iso(row.get("discovered_utc", ""))
    if not dt:
        return ""
    return dt.strftime("%Y-%m")


def safe_float(value: str) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def load_rows() -> list[dict]:
    if not CLOSURES_CSV.exists():
        return []
    with CLOSURES_CSV.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build_payload(rows: list[dict]) -> dict:
    by_chain = Counter()
    by_state = Counter()
    by_month = Counter()
    by_source = Counter()
    with_coords = 0

    normalized_rows = []
    for row in rows:
        chain = (row.get("chain") or "").strip()
        state = (row.get("state") or "").strip()
        month = pick_date_key(row)
        source_domain = (row.get("source_domain") or "").strip()
        lat = safe_float(row.get("latitude", ""))
        lon = safe_float(row.get("longitude", ""))

        if chain:
            by_chain[chain] += 1
        if state:
            by_state[state] += 1
        if month:
            by_month[month] += 1
        if source_domain:
            by_source[source_domain] += 1
        if lat is not None and lon is not None:
            with_coords += 1

        normalized_rows.append(
            {
                "record_id": row.get("record_id", ""),
                "chain": chain,
                "location_text": row.get("location_text", ""),
                "city": row.get("city", ""),
                "state": state,
                "latitude": lat,
                "longitude": lon,
                "article_title": row.get("article_title", ""),
                "article_url": row.get("article_url", ""),
                "source_domain": source_domain,
                "source_type": row.get("source_type", ""),
                "published_utc": row.get("published_utc", ""),
                "discovered_utc": row.get("discovered_utc", ""),
                "confidence": safe_float(row.get("confidence", "")),
                "evidence_snippet": row.get("evidence_snippet", ""),
                "query": row.get("query", ""),
            }
        )

    records_by_month = [{"month": k, "count": by_month[k]} for k in sorted(by_month.keys())]
    records_by_chain = [{"chain": k, "count": by_chain[k]} for k in sorted(by_chain.keys())]
    records_by_state = [{"state": k, "count": by_state[k]} for k in by_state.keys()]
    records_by_state.sort(key=lambda x: x["count"], reverse=True)
    records_by_source = [{"source_domain": k, "count": by_source[k]} for k in by_source.keys()]
    records_by_source.sort(key=lambda x: x["count"], reverse=True)

    payload = {
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "totals": {
            "records": len(normalized_rows),
            "with_coordinates": with_coords,
            "without_coordinates": max(0, len(normalized_rows) - with_coords),
        },
        "breakdowns": {
            "by_chain": records_by_chain,
            "by_month": records_by_month,
            "by_state": records_by_state,
            "by_source_domain": records_by_source,
        },
        "records": normalized_rows,
    }
    return payload


def main() -> None:
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    payload = build_payload(rows)
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Rows loaded: {len(rows)}")
    print(f"Wrote dashboard data: {OUT_JSON}")


if __name__ == "__main__":
    main()
