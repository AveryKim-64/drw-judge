#!/usr/bin/env python3
"""Fetch results from a Dune Analytics query via the Dune API.

WHY THIS EXISTS
---------------
dune.com blocks automated page fetches (403 on plain HTTP clients).
The Dune REST API, however, is open to anyone with a free account.
This script is the thin wrapper we use instead of scraping.

QUICK START
-----------
    python3 getDune.py 3237025                 # cached result, no quota used
    python3 getDune.py 3237025 --execute       # fresh run, consumes 1 execution credit
    python3 getDune.py --url https://dune.com/queries/3237025
    python3 getDune.py 3237025 --save out.json # write JSON to a file

The script auto-loads DUNE_API_KEY from ./.env — no need to `source .env`
first. `python3 getDune.py --help` lists all flags.

HOW TO FIND A QUERY ID
----------------------
The API is keyed on QUERY IDs, not DASHBOARD URLs. There is no
"fetch a whole dashboard" endpoint. To pull the data behind a chart:

    1. Open the dashboard on dune.com in a browser.
    2. Click the chart/panel you want — it drills into the underlying query.
    3. The URL becomes https://dune.com/queries/<numeric_id>.
    4. Pass that ID (or the full URL via --url) to this script.

A dashboard is usually backed by multiple queries (one per panel), so you
may need to run the script several times to reproduce a full dashboard.

OUTPUT SHAPE
------------
The JSON response has (abbreviated):

    {
      "execution_id": "...",
      "query_id": 3237025,
      "state": "QUERY_STATE_COMPLETED",
      "result": {
        "rows": [ {...}, {...} ],            # <- the data you want
        "metadata": { "column_names": [...], "row_count": N, ... }
      }
    }

For large result sets, use --save to avoid flooding your terminal.

QUOTA
-----
Default mode (no --execute) hits /api/v1/query/<id>/results and returns
the LAST cached result. This does not consume execution credits.
--execute triggers a fresh run and will count against your Dune plan's
monthly execution quota (free tier is limited — check dune.com/pricing).
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

API_BASE = "https://api.dune.com/api/v1"


def load_env(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, optional `export` prefix and quotes."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def query_id_from_url(url: str) -> int:
    m = re.search(r"/queries/(\d+)", url)
    if not m:
        sys.exit(f"Could not find a numeric query ID in URL: {url}")
    return int(m.group(1))


def fetch_latest(query_id: int, key: str) -> dict:
    r = requests.get(
        f"{API_BASE}/query/{query_id}/results",
        headers={"X-Dune-API-Key": key},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def execute_and_wait(query_id: int, key: str, poll_interval: float = 2.0) -> dict:
    headers = {"X-Dune-API-Key": key}
    r = requests.post(f"{API_BASE}/query/{query_id}/execute", headers=headers, timeout=30)
    r.raise_for_status()
    execution_id = r.json()["execution_id"]

    while True:
        r = requests.get(f"{API_BASE}/execution/{execution_id}/status", headers=headers, timeout=30)
        r.raise_for_status()
        state = r.json().get("state")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            sys.exit(f"Execution {execution_id} ended with state {state}")
        time.sleep(poll_interval)

    r = requests.get(f"{API_BASE}/execution/{execution_id}/results", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Dune query results via the API.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("query_id", nargs="?", type=int, help="Numeric Dune query ID")
    g.add_argument("--url", help="Dune query URL (e.g. https://dune.com/queries/1234567)")
    ap.add_argument("--execute", action="store_true", help="Force fresh execution (consumes quota)")
    ap.add_argument("--save", type=Path, help="Write JSON result to this path")
    args = ap.parse_args()

    load_env(Path(__file__).resolve().parent / ".env")
    key = os.environ.get("DUNE_API_KEY")
    if not key:
        sys.exit("DUNE_API_KEY not set. Add it to .env or export it.")

    qid = args.query_id if args.query_id is not None else query_id_from_url(args.url)
    result = execute_and_wait(qid, key) if args.execute else fetch_latest(qid, key)

    text = json.dumps(result, indent=2)
    if args.save:
        args.save.write_text(text)
        print(f"Saved to {args.save}")
    else:
        print(text)


if __name__ == "__main__":
    main()
