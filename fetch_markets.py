#!/usr/bin/env python3
"""Fetch and cache prediction-market contracts from Kalshi and Polymarket.

WHY THIS EXISTS
---------------
A single command that pulls from either venue with consistent flags and
caches the raw JSON to disk — so exploratory work and training-data
collection don't depend on remembering curl flags and jq.

QUICK START
-----------
    python3 fetch_markets.py kalshi --series KXFED
    python3 fetch_markets.py kalshi --event KXFEDDECISION-27DEC --limit 20
    python3 fetch_markets.py kalshi --ticker KXFED-27JAN-T4.25
    python3 fetch_markets.py polymarket --grep fed --limit 500
    python3 fetch_markets.py polymarket --id 616906
    python3 fetch_markets.py polymarket --stdout          # print JSON, don't save

CACHE LAYOUT
------------
Default: data/snapshots/{venue}/{UTC-ISO-timestamp}.json plus a short
summary printed to stdout. Override with --out PATH, or skip the write
entirely with --stdout.

SEE ALSO
--------
    canonical.py            — schema these markets will be mapped into
    docs/project_brief.md   — project context and venue-specific gotchas
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_BASE = "https://gamma-api.polymarket.com"
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = REPO_ROOT / "data" / "snapshots"


def fetch_kalshi(*, limit=100, status="open", series_ticker=None, event_ticker=None, ticker=None):
    if ticker:
        r = requests.get(f"{KALSHI_BASE}/markets/{ticker}", timeout=30)
        r.raise_for_status()
        m = r.json().get("market")
        return [m] if m else []
    params = {"limit": limit, "status": status}
    if series_ticker:
        params["series_ticker"] = series_ticker
    if event_ticker:
        params["event_ticker"] = event_ticker
    r = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("markets", [])


def fetch_polymarket(*, limit=100, market_id=None, include_closed=False, order="volume"):
    if market_id:
        r = requests.get(f"{POLY_BASE}/markets/{market_id}", timeout=30)
        r.raise_for_status()
        body = r.json()
        return [body] if isinstance(body, dict) else body
    params = {
        "limit": limit,
        "active": "true",
        "closed": "true" if include_closed else "false",
    }
    if order:
        params["order"] = order
        params["ascending"] = "false"
    r = requests.get(f"{POLY_BASE}/markets", params=params, timeout=30)
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else []


def grep_markets(markets, needle, keys):
    n = needle.lower()
    out = []
    for m in markets:
        for k in keys:
            v = m.get(k)
            if isinstance(v, str) and n in v.lower():
                out.append(m)
                break
    return out


def summarize(markets, venue, limit=10):
    for m in markets[:limit]:
        if venue == "kalshi":
            tag = m.get("ticker", "")
            label = m.get("yes_sub_title") or m.get("title", "")
        else:
            tag = str(m.get("id", ""))
            label = m.get("question", "")
        print(f"  {tag:48s}  {label[:70]}")


def write_snapshot(payload, venue, cache_dir, out_path=None):
    if out_path:
        path = Path(out_path)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = cache_dir / venue / f"{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def main():
    ap = argparse.ArgumentParser(
        description="Fetch Kalshi/Polymarket markets and cache snapshots.",
    )
    sub = ap.add_subparsers(dest="venue", required=True)

    k = sub.add_parser("kalshi", help="Fetch Kalshi markets")
    k.add_argument("--limit", type=int, default=100)
    k.add_argument("--status", default="open", help="open | closed | settled (default: open)")
    k.add_argument("--series", dest="series_ticker", help="Filter by series ticker (e.g. KXFED)")
    k.add_argument("--event", dest="event_ticker", help="Filter by event ticker (e.g. KXFEDDECISION-27DEC)")
    k.add_argument("--ticker", help="Fetch a single market by ticker")

    p = sub.add_parser("polymarket", help="Fetch Polymarket markets")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--order", default="volume", help="Order field, always descending (default: volume)")
    p.add_argument("--include-closed", action="store_true", help="Include closed markets")
    p.add_argument("--id", dest="market_id", help="Fetch a single market by id")

    for sp in (k, p):
        sp.add_argument("--grep", help="Client-side filter on title/question (case-insensitive)")
        sp.add_argument("--out", help="Write snapshot to this path instead of the default cache dir")
        sp.add_argument("--stdout", action="store_true", help="Print full JSON to stdout; do not save")
        sp.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)

    args = ap.parse_args()

    if args.venue == "kalshi":
        markets = fetch_kalshi(
            limit=args.limit,
            status=args.status,
            series_ticker=args.series_ticker,
            event_ticker=args.event_ticker,
            ticker=args.ticker,
        )
        grep_keys = ("title", "yes_sub_title", "rules_primary")
    else:
        markets = fetch_polymarket(
            limit=args.limit,
            market_id=args.market_id,
            include_closed=args.include_closed,
            order=args.order,
        )
        grep_keys = ("question", "description")

    if args.grep:
        markets = grep_markets(markets, args.grep, grep_keys)

    print(f"Fetched {len(markets)} {args.venue} markets", file=sys.stderr)

    payload = {
        "venue": args.venue,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(markets),
        "markets": markets,
    }

    if args.stdout:
        print(json.dumps(payload, indent=2))
    else:
        path = write_snapshot(payload, args.venue, args.cache_dir, out_path=args.out)
        print(f"Saved to {path}", file=sys.stderr)
        summarize(markets, args.venue)


if __name__ == "__main__":
    main()
