#!/usr/bin/env python3
"""Extract CanonicalContract from raw Polymarket market dicts via LLM.

Kalshi fields are machine-readable so compare_pair.py handles them structurally.
Polymarket buries all load-bearing semantics (occurrence type, threshold,
unscheduled-event treatment) in free-text `description`. This script drives
Claude to fill in the canonical schema from that prose using tool use so the
response is always structured JSON — no regex on freeform output.

Extractions are cached to data/canonical/polymarket/ as JSONL — one record per
market id — so judge.py never pays extraction cost twice for the same market.

USAGE
-----
    python3 extract.py data/snapshots/polymarket/fed_fomc.json
    python3 extract.py data/snapshots/polymarket/*.json
    python3 extract.py data/snapshots/polymarket/fed_fomc.json --out data/canonical/polymarket/fed.jsonl
    python3 extract.py data/snapshots/polymarket/fed_fomc.json --stdout   # print, no write
    python3 extract.py --id 616906                                         # fetch live + extract

REQUIRES
--------
    pip install anthropic   (already in requirements.txt)
    ANTHROPIC_API_KEY in .env or environment
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests

REPO_ROOT = Path(__file__).resolve().parent
CACHE_DIR = REPO_ROOT / "data" / "canonical" / "polymarket"
POLY_BASE = "https://gamma-api.polymarket.com"

MODEL = "claude-sonnet-4-6"


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

# ---------------------------------------------------------------------------
# Tool schema — Claude fills this in; we deserialise into CanonicalContract
# ---------------------------------------------------------------------------

_EXTRACT_TOOL: dict = {
    "name": "extract_canonical",
    "description": (
        "Fill in every field of the canonical contract schema from the Polymarket "
        "market text. Be precise about occurrence.type and includes_unscheduled — "
        "these are the two fields most likely to cause mis-labelling downstream."
    ),
    "input_schema": {
        "type": "object",
        "required": ["anchor", "occurrence", "measurement", "shape", "resolution"],
        "properties": {
            "anchor": {
                "type": "object",
                "required": ["subject", "subject_type"],
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Concise name of what is being measured, e.g. 'US federal funds rate'.",
                    },
                    "subject_type": {
                        "type": "string",
                        "enum": ["rate", "price", "winner", "count", "outcome", "occurrence"],
                    },
                    "jurisdiction": {
                        "type": "string",
                        "description": "Governing body or scope, e.g. 'FOMC'. Omit if not applicable.",
                    },
                },
            },
            "occurrence": {
                "type": "object",
                "required": ["type"],
                "description": (
                    "WHEN the outcome is evaluated. "
                    "point=single moment; interval=open window; "
                    "cumulative=count over a range; sequence=ordered list of named events."
                ),
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["point", "interval", "cumulative", "sequence"],
                    },
                    "point_time": {
                        "type": "string",
                        "description": "ISO 8601 datetime. Use when type=point.",
                    },
                    "interval": {
                        "type": "object",
                        "required": ["start", "end"],
                        "properties": {
                            "start": {"type": "string"},
                            "end": {"type": "string"},
                        },
                    },
                    "sequence": {
                        "type": "array",
                        "description": "Ordered sub-events for type=sequence.",
                        "items": {
                            "type": "object",
                            "required": ["type"],
                            "properties": {
                                "type": {"type": "string", "enum": ["point"]},
                                "point_time": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "measurement": {
                "type": "object",
                "required": ["metric", "threshold", "includes_unscheduled"],
                "properties": {
                    "metric": {
                        "type": "string",
                        "description": (
                            "Standardised snake_case name for what is measured. "
                            "Examples: count_of_25bp_cuts, target_rate_upper_bound, "
                            "fed_funds_rate_change_bps, btc_price_usd."
                        ),
                    },
                    "threshold": {
                        "type": "object",
                        "required": ["op", "value"],
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["gt", "gte", "lt", "lte", "eq", "between", "is"],
                            },
                            "value": {
                                "description": (
                                    "Number, string, or [low, high] array for 'between'. "
                                    "Use a string like 'Cut-Pause-Pause' for categorical sequences."
                                ),
                            },
                        },
                    },
                    "unit": {
                        "type": "string",
                        "description": "e.g. 'bps', 'usd', 'count', 'percent'.",
                    },
                    "includes_unscheduled": {
                        "type": "boolean",
                        "description": (
                            "True only if the description explicitly says emergency or "
                            "unscheduled actions COUNT. False if they are excluded or "
                            "the description is silent."
                        ),
                    },
                },
            },
            "shape": {
                "type": "object",
                "required": ["outcome_type", "outcomes"],
                "properties": {
                    "outcome_type": {
                        "type": "string",
                        "enum": ["binary", "scalar_tree", "categorical"],
                    },
                    "outcomes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The actual outcome labels, e.g. ['Yes', 'No'] or ['Cut-Pause-Pause', 'Other'].",
                    },
                    "mece_group": {
                        "type": "string",
                        "description": (
                            "Stable group identifier if this is one leg of a MECE scalar tree. "
                            "Use the Polymarket negRiskMarketID if present, otherwise omit."
                        ),
                    },
                },
            },
            "resolution": {
                "type": "object",
                "required": ["authority", "trading_cutoff", "evaluation_time"],
                "properties": {
                    "authority": {
                        "type": "string",
                        "description": "Who/what determines resolution, e.g. 'FOMC post-meeting statements'.",
                    },
                    "trading_cutoff": {
                        "type": "string",
                        "description": "ISO 8601 datetime — copy endDate from the market.",
                    },
                    "evaluation_time": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime — when the outcome is evaluated. "
                            "Usually equal to or shortly after the last event in the sequence."
                        ),
                    },
                    "data_source": {
                        "type": "string",
                        "description": "URL of official data source if one is cited in the description.",
                    },
                },
            },
        },
    },
}

_SYSTEM_PROMPT = """\
You are a canonical-schema extractor for prediction-market contracts.

Given a Polymarket market (question, groupItemTitle, endDate, description,
outcomes, resolutionSource), call the extract_canonical tool to fill in the
canonical contract schema.

## Critical fields — read the description carefully

`occurrence.type` — the most common mis-label:
  - point      : resolves on a single future moment (one FOMC meeting, one date)
  - interval   : resolves any time within an open window
  - cumulative : counts events over a date range (e.g. "4 cuts in 2026")
  - sequence   : resolves on an ordered conjunction of named events
                 (e.g. "Cut-Pause-Pause at three specific meetings")

`measurement.includes_unscheduled`:
  - True  ONLY if the description explicitly says emergency / unscheduled
          actions COUNT toward resolution.
  - False if they are excluded ("Emergency rate cuts...will NOT be considered")
          OR if the description says nothing about them.

`measurement.threshold.op`:
  - Use "is" for categorical/qualitative outcomes (e.g. a specific sequence name).
  - Use "eq" for an exact numeric count or level.
  - Use "gt"/"gte"/"lt"/"lte" for one-sided comparisons.
  - Use "between" with a [low, high] array for range contracts.

`shape.outcome_type`:
  - binary      : Yes/No
  - scalar_tree : one leg of a MECE set (e.g. rate at exactly 4.25%)
  - categorical : named non-numeric outcomes (e.g. Cut-Pause-Pause / Other)

Fill in every required field. For optional fields (jurisdiction, unit,
mece_group, data_source), include them only when clearly present.\
"""


# ---------------------------------------------------------------------------
# Core extraction function
# ---------------------------------------------------------------------------

def _market_text(m: dict) -> str:
    """Build the user-turn content from a raw Polymarket market dict."""
    outcomes_raw = m.get("outcomes", "[]")
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    except json.JSONDecodeError:
        outcomes = outcomes_raw

    prices_raw = m.get("outcomePrices", "[]")
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
    except json.JSONDecodeError:
        prices = prices_raw

    parts = [
        f"id: {m.get('id', '')}",
        f"question: {m.get('question', '')}",
        f"groupItemTitle: {m.get('groupItemTitle', '')}",
        f"endDate: {m.get('endDate', '')}",
        f"outcomes: {json.dumps(outcomes)}",
        f"outcomePrices: {json.dumps(prices)}",
        f"resolutionSource: {m.get('resolutionSource') or '(empty)'}",
        f"negRiskMarketID: {m.get('negRiskMarketID') or '(none)'}",
        "",
        "description:",
        (m.get("description") or "(empty)"),
    ]
    return "\n".join(parts)


def extract_canonical(m: dict, client: anthropic.Anthropic) -> dict:
    """Return the extracted canonical dict for a single Polymarket market.

    Raises ValueError if the model does not call the tool.
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_canonical"},
        messages=[{"role": "user", "content": _market_text(m)}],
    )

    usage = response.usage
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    print(
        f"    tokens: in={usage.input_tokens} "
        f"cache_write={cache_write} cache_read={cache_read} "
        f"out={usage.output_tokens}",
        file=sys.stderr,
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_canonical":
            return block.input

    raise ValueError(f"Model did not call extract_canonical for market {m.get('id')}")


# ---------------------------------------------------------------------------
# Snapshot loading
# ---------------------------------------------------------------------------

def load_snapshot(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    markets = data.get("markets", data) if isinstance(data, dict) else data
    return markets if isinstance(markets, list) else []


def fetch_live(market_id: str) -> dict:
    r = requests.get(f"{POLY_BASE}/markets/{market_id}", timeout=30)
    if r.status_code == 404:
        sys.exit(f"Polymarket market not found: {market_id}")
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_cache(path: Path) -> dict[str, dict]:
    """Return {market_id: record} from an existing JSONL cache file."""
    if not path.exists():
        return {}
    cache: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            cache[str(rec["polymarket_id"])] = rec
        except (json.JSONDecodeError, KeyError):
            pass
    return cache


def write_record(fh, market_id: str, canonical: dict) -> None:
    record = {
        "polymarket_id": str(market_id),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "canonical": canonical,
    }
    fh.write(json.dumps(record) + "\n")
    fh.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract CanonicalContract from Polymarket markets via LLM."
    )
    ap.add_argument(
        "snapshots", nargs="*", type=Path, metavar="SNAPSHOT",
        help="Polymarket snapshot JSON file(s). Omit when using --id.",
    )
    ap.add_argument(
        "--id", metavar="MARKET_ID",
        help="Fetch a single live market by id and extract it.",
    )
    ap.add_argument(
        "--out", type=Path, metavar="PATH",
        help="Output JSONL path. Default: data/canonical/polymarket/<input_stem>.jsonl",
    )
    ap.add_argument(
        "--stdout", action="store_true",
        help="Print extractions to stdout instead of writing a file.",
    )
    ap.add_argument(
        "--no-cache", action="store_true",
        help="Re-extract even if the market id is already in the output file.",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env or export it.")

    client = anthropic.Anthropic()

    # ---- single live market ----
    if args.id:
        m = fetch_live(args.id)
        print(f"Extracting market {args.id}: {m.get('question', '')[:80]}", file=sys.stderr)
        canonical = extract_canonical(m, client)
        record = {
            "polymarket_id": str(args.id),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "canonical": canonical,
        }
        print(json.dumps(record, indent=2))
        return

    if not args.snapshots:
        ap.error("Provide at least one snapshot file or use --id.")

    # ---- batch over snapshot files ----
    for snap_path in args.snapshots:
        if not snap_path.exists():
            print(f"warn: {snap_path} not found, skipping", file=sys.stderr)
            continue

        markets = load_snapshot(snap_path)
        print(f"\n{snap_path.name}: {len(markets)} markets", file=sys.stderr)

        if args.stdout:
            out_path = None
            cache: dict[str, dict] = {}
            fh = sys.stdout
        else:
            out_path = args.out or (CACHE_DIR / (snap_path.stem + ".jsonl"))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            cache = {} if args.no_cache else load_cache(out_path)
            fh = out_path.open("a")

        skipped = 0
        extracted = 0
        errors = 0

        try:
            for m in markets:
                mid = str(m.get("id", ""))
                if not mid:
                    continue
                if mid in cache and not args.no_cache:
                    skipped += 1
                    continue

                q = (m.get("question") or "")[:80]
                print(f"  [{mid}] {q}", file=sys.stderr)
                try:
                    canonical = extract_canonical(m, client)
                    if args.stdout:
                        record = {
                            "polymarket_id": mid,
                            "extracted_at": datetime.now(timezone.utc).isoformat(),
                            "canonical": canonical,
                        }
                        fh.write(json.dumps(record) + "\n")
                    else:
                        write_record(fh, mid, canonical)
                    extracted += 1
                except Exception as e:
                    print(f"    ERROR: {e}", file=sys.stderr)
                    errors += 1
        finally:
            if not args.stdout:
                fh.close()

        print(
            f"  done: {extracted} extracted, {skipped} skipped (cached), {errors} errors",
            file=sys.stderr,
        )
        if out_path:
            print(f"  → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
