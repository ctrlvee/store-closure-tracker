from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CLOSURES_CSV = DATA_DIR / "closures.csv"
GEOCODE_CACHE_JSON = DATA_DIR / "geocode_cache.json"

USER_AGENT = "store-closure-tracker/1.0 (+https://github.com)"
REQUEST_TIMEOUT = 25

US_STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
CITY_STATE_RE = re.compile(rf"\b([A-Z][a-z]+(?:[\s-][A-Z][a-z]+)*,\s?(?:{US_STATE_CODES}))\b")
ADDRESS_RE = re.compile(
    rf"\b(\d{{2,6}}\s+[A-Z0-9][A-Za-z0-9.\-\s]{{3,80}}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct),\s*[A-Z][a-z]+(?:[\s-][A-Z][a-z]+)*,\s*(?:{US_STATE_CODES}))\b"
)

FEED_QUERIES = {
    "CVS": [
        "CVS store closure location",
        "CVS closing stores",
        "CVS pharmacy closed location",
    ],
    "Walgreens": [
        "Walgreens store closure location",
        "Walgreens closing stores",
        "Walgreens pharmacy closed location",
    ],
}

CLOSURE_TERMS_STRONG = [
    "store closure",
    "store closings",
    "closing stores",
    "to close",
    "will close",
    "closed permanently",
    "shuttered",
]

CLOSURE_TERMS_WEAK = [
    "closure",
    "close",
    "closed",
    "shutdown",
    "shut down",
    "retail exit",
]

CSV_HEADERS = [
    "record_id",
    "chain",
    "location_text",
    "city",
    "state",
    "latitude",
    "longitude",
    "article_title",
    "article_url",
    "source_domain",
    "source_type",
    "published_utc",
    "discovered_utc",
    "confidence",
    "evidence_snippet",
    "query",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def safe_get_json(url: str, params: Dict[str, str] | None = None) -> Dict:
    try:
        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def fetch_gdelt_articles(query: str, max_per_query: int) -> Iterable[Dict[str, str]]:
    payload = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max_per_query),
        "sort": "DateDesc",
    }
    data = safe_get_json("https://api.gdeltproject.org/api/v2/doc/doc", params=payload)
    for article in data.get("articles", []):
        url = article.get("url") or ""
        if not url:
            continue
        yield {
            "title": normalize_whitespace(article.get("title", "")),
            "url": url,
            "published": normalize_whitespace(article.get("seendate", "")),
            "summary": normalize_whitespace(article.get("socialimage", "")),
            "source_type": "gdelt",
        }


def fetch_bing_rss_articles(query: str, max_per_query: int) -> Iterable[Dict[str, str]]:
    rss_url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=RSS"
    feed = feedparser.parse(rss_url)
    for entry in feed.entries[:max_per_query]:
        url = entry.get("link", "")
        if not url:
            continue
        published = ""
        if getattr(entry, "published_parsed", None):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        yield {
            "title": normalize_whitespace(entry.get("title", "")),
            "url": url,
            "published": published,
            "summary": normalize_whitespace(entry.get("summary", "")),
            "source_type": "bing_rss",
        }


def fetch_article_text(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for bad in soup(["script", "style", "noscript"]):
            bad.extract()
        paragraphs = [normalize_whitespace(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
        text = " ".join(p for p in paragraphs if p)
        return normalize_whitespace(text)[:30000]
    except Exception:
        return ""


def closure_confidence(text: str) -> float:
    t = (text or "").lower()
    score = 0.0
    for kw in CLOSURE_TERMS_STRONG:
        if kw in t:
            score += 0.35
    for kw in CLOSURE_TERMS_WEAK:
        if kw in t:
            score += 0.12
    return min(score, 1.0)


def extract_locations(text: str) -> List[str]:
    seen = set()
    ordered = []
    for pattern in (ADDRESS_RE, CITY_STATE_RE):
        for match in pattern.findall(text or ""):
            loc = normalize_whitespace(match.rstrip(".,;:"))
            if len(loc) < 4:
                continue
            key = loc.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(loc)
    return ordered[:12]


def extract_city_state(location_text: str) -> tuple[str, str]:
    m = CITY_STATE_RE.search(location_text or "")
    if not m:
        return "", ""
    city, state = [part.strip() for part in m.group(1).split(",", 1)]
    return city, state


def extract_evidence_snippet(text: str) -> str:
    cleaned = normalize_whitespace(text)
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    for sentence in sentences:
        low = sentence.lower()
        if "close" in low or "closure" in low or "shutter" in low:
            return sentence[:260]
    return cleaned[:260]


def load_geocode_cache() -> Dict[str, Dict[str, str]]:
    if not GEOCODE_CACHE_JSON.exists():
        return {}
    try:
        return json.loads(GEOCODE_CACHE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_geocode_cache(cache: Dict[str, Dict[str, str]]) -> None:
    GEOCODE_CACHE_JSON.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def geocode_location(location_text: str, cache: Dict[str, Dict[str, str]]) -> tuple[str, str]:
    if not location_text:
        return "", ""
    key = location_text.strip().lower()
    if key in cache:
        return cache[key].get("lat", ""), cache[key].get("lon", "")
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": location_text,
                "format": "jsonv2",
                "limit": "1",
                "countrycodes": "us",
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        rows = response.json()
        if rows:
            lat = str(rows[0].get("lat", ""))
            lon = str(rows[0].get("lon", ""))
        else:
            lat = ""
            lon = ""
    except Exception:
        lat = ""
        lon = ""
    cache[key] = {"lat": lat, "lon": lon}
    # Respect Nominatim public usage policy.
    time.sleep(1)
    return lat, lon


def make_record_id(article_url: str, location_text: str) -> str:
    base = f"{article_url.strip()}|{location_text.strip().lower()}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:18]


def load_existing_record_ids() -> set[str]:
    if not CLOSURES_CSV.exists():
        return set()
    ids = set()
    with CLOSURES_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            value = (row.get("record_id") or "").strip()
            if value:
                ids.add(value)
    return ids


def ensure_csv_header() -> None:
    if CLOSURES_CSV.exists():
        return
    with CLOSURES_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()


def collect_candidates(max_per_query: int) -> List[Dict[str, str]]:
    combined: Dict[str, Dict[str, str]] = {}
    for chain, queries in FEED_QUERIES.items():
        for query in queries:
            for row in fetch_gdelt_articles(query, max_per_query):
                key = f"{chain}|{row['url']}"
                if key not in combined:
                    row["chain"] = chain
                    row["query"] = query
                    combined[key] = row
            for row in fetch_bing_rss_articles(query, max_per_query):
                key = f"{chain}|{row['url']}"
                if key not in combined:
                    row["chain"] = chain
                    row["query"] = query
                    combined[key] = row
    return list(combined.values())


def source_domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def build_rows(
    candidates: List[Dict[str, str]],
    existing_ids: set[str],
    skip_geocode: bool,
) -> List[Dict[str, str]]:
    discovered_utc = utc_now_iso()
    geocode_cache = load_geocode_cache()
    rows: List[Dict[str, str]] = []

    for item in candidates:
        article_url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        summary = item.get("summary", "").strip()
        chain = item.get("chain", "").strip()
        if not article_url or not title or not chain:
            continue

        article_text = fetch_article_text(article_url)
        full_text = normalize_whitespace(" ".join([title, summary, article_text]))
        score = closure_confidence(full_text)
        if score < 0.35:
            continue

        locations = extract_locations(full_text)
        if not locations:
            locations = [""]

        evidence = extract_evidence_snippet(full_text)
        for loc in locations:
            record_id = make_record_id(article_url, loc)
            if record_id in existing_ids:
                continue

            city, state = extract_city_state(loc)
            if loc and not skip_geocode:
                lat, lon = geocode_location(loc, geocode_cache)
            else:
                lat, lon = "", ""

            if loc:
                confidence = min(1.0, score + 0.2)
            else:
                confidence = min(1.0, score - 0.1)

            rows.append(
                {
                    "record_id": record_id,
                    "chain": chain,
                    "location_text": loc,
                    "city": city,
                    "state": state,
                    "latitude": lat,
                    "longitude": lon,
                    "article_title": title,
                    "article_url": article_url,
                    "source_domain": source_domain(article_url),
                    "source_type": item.get("source_type", ""),
                    "published_utc": item.get("published", ""),
                    "discovered_utc": discovered_utc,
                    "confidence": f"{confidence:.2f}",
                    "evidence_snippet": evidence,
                    "query": item.get("query", ""),
                }
            )
            existing_ids.add(record_id)

    save_geocode_cache(geocode_cache)
    return rows


def append_rows(rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    with CLOSURES_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        for row in rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect reported CVS/Walgreens closure mentions.")
    parser.add_argument(
        "--max-per-query",
        type=int,
        default=80,
        help="Max articles per query per source (default: 80).",
    )
    parser.add_argument(
        "--skip-geocode",
        action="store_true",
        help="Skip geocoding for faster runs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_csv_header()

    existing_ids = load_existing_record_ids()
    candidates = collect_candidates(max_per_query=max(10, args.max_per_query))
    rows = build_rows(candidates, existing_ids=existing_ids, skip_geocode=args.skip_geocode)
    append_rows(rows)

    print(f"Candidates collected: {len(candidates)}")
    print(f"New closure rows added: {len(rows)}")


if __name__ == "__main__":
    main()
