#!/usr/bin/env python3
"""LLM-based judge for cross-venue contract matching.

Takes two contract dicts (one Kalshi, one Polymarket) and returns a
MatchLabel string: EQUIVALENT, SUBSET, SYNTHETIC, or UNRELATED.

The system prompt (label taxonomy + decision rules) is stable and cached
via cache_control — repeated calls in the same session pay only for the
contract pair, not the full prompt.

USAGE
-----
    export ANTHROPIC_API_KEY=sk-ant-...   # or put in .env
    python3 judge.py                      # runs built-in test pairs

REQUIRES
--------
    pip install anthropic
"""

from __future__ import annotations

import json
import os
import sys

import anthropic

from canonical import MatchLabel


def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

_SYSTEM_PROMPT = """\
You are a semantic matching judge for prediction-market contracts.

Given a Kalshi contract and a Polymarket contract, decide whether they are
equivalent for the purpose of cross-venue arbitrage.

## Label taxonomy

EQUIVALENT   Same event, same threshold, same time window, same yes/no polarity.
             A YES on one perfectly hedges with a NO on the other.

SUBSET       One contract covers a strict subset of what the other covers.
             Classic trap: Kalshi asks about one FOMC meeting; Polymarket asks
             about the full calendar year. The single meeting is a subset.

SYNTHETIC    Different underlying events but a tight mathematical relationship
             (complementary legs of a scalar tree, or a linear combination).

UNRELATED    Different subject, incompatible threshold, or no systematic
             price relationship.

## Decision rules

1. `anchor` (what) — if the subject differs, lean UNRELATED.
2. `occurrence` (when) — point vs cumulative is the most common SUBSET trap.
3. `measurement.threshold` — a 25 bp cut vs 50 bp cut at the same meeting is UNRELATED.
4. `shape` — binary YES/NO can be EQUIVALENT to a scalar-tree leg at the same threshold.
5. Ignore cosmetic differences: title phrasing, data source URLs, trading cutoff
   precision (±1 min), venue-specific ID formats.

## Output format

Reply with exactly one word on a single line, nothing else:
EQUIVALENT
SUBSET
SYNTHETIC
UNRELATED\
"""


def judge_pair(kalshi: dict, polymarket: dict) -> str:
    """Return a MatchLabel string for a Kalshi / Polymarket contract pair.

    The system prompt is cached — repeated calls within the 5-minute TTL
    pay only for the contract pair tokens.
    """
    client = anthropic.Anthropic()

    user_content = (
        f"## Kalshi contract\n{json.dumps(kalshi, indent=2, default=str)}\n\n"
        f"## Polymarket contract\n{json.dumps(polymarket, indent=2, default=str)}"
    )

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=16,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip().upper()
    valid = {m.value.upper() for m in MatchLabel}
    if raw not in valid:
        raise ValueError(f"Unexpected judge output: {raw!r}")

    cache_read = response.usage.cache_read_input_tokens or 0
    cache_write = response.usage.cache_creation_input_tokens or 0
    print(
        f"  tokens: in={response.usage.input_tokens} "
        f"cache_write={cache_write} cache_read={cache_read} "
        f"out={response.usage.output_tokens}"
    )
    return raw


# ---------------------------------------------------------------------------
# Test harness — 3 pairs covering the main label classes
# ---------------------------------------------------------------------------

_TEST_PAIRS = [
    {
        "name": "Fed cuts 2026 — annual cumulative vs single meeting (SUBSET)",
        "kalshi": {
            "ticker": "KXFEDDECISION-27JAN-C25",
            "title": "Will the Fed cut by 25 bps at the January 2027 meeting?",
            "anchor": {"subject": "US federal funds rate", "subject_type": "rate"},
            "occurrence": {"type": "point", "point_time": "2027-01-28T19:00:00Z"},
            "measurement": {
                "metric": "fed_funds_rate_change_bps",
                "threshold": {"op": "eq", "value": -25},
            },
            "shape": "binary",
        },
        "polymarket": {
            "id": "616906",
            "title": "Will the Fed cut rates 4 times in 2026?",
            "anchor": {"subject": "US federal funds rate", "subject_type": "rate"},
            "occurrence": {
                "type": "cumulative",
                "interval": {"start": "2026-01-01", "end": "2026-12-31"},
            },
            "measurement": {
                "metric": "count_of_25bp_cuts",
                "threshold": {"op": "eq", "value": 4},
            },
            "shape": "binary",
        },
        "expected": "SUBSET",
    },
    {
        "name": "Same FOMC meeting, same 25 bp threshold (EQUIVALENT)",
        "kalshi": {
            "ticker": "KXFEDDECISION-27MAR-C25",
            "title": "Fed cuts 25 bps at March 2027 FOMC meeting?",
            "anchor": {"subject": "US federal funds rate", "subject_type": "rate"},
            "occurrence": {"type": "point", "point_time": "2027-03-19T19:00:00Z"},
            "measurement": {
                "metric": "fed_funds_rate_change_bps",
                "threshold": {"op": "eq", "value": -25},
            },
            "shape": "binary",
        },
        "polymarket": {
            "id": "999001",
            "title": "Will the Fed cut by 25 basis points at the March 2027 meeting?",
            "anchor": {"subject": "US federal funds rate", "subject_type": "rate"},
            "occurrence": {"type": "point", "point_time": "2027-03-19"},
            "measurement": {
                "metric": "fed_funds_rate_change_bps",
                "threshold": {"op": "eq", "value": -25},
            },
            "shape": "binary",
        },
        "expected": "EQUIVALENT",
    },
    {
        "name": "Bitcoin price vs Fed rate cuts — clearly different subjects (UNRELATED)",
        "kalshi": {
            "ticker": "KXBTC-27JAN-T50000",
            "title": "Will Bitcoin be above $50,000 on Jan 1 2027?",
            "anchor": {"subject": "Bitcoin USD price", "subject_type": "price"},
            "occurrence": {"type": "point", "point_time": "2027-01-01T00:00:00Z"},
            "measurement": {
                "metric": "btc_usd_spot",
                "threshold": {"op": "gt", "value": 50000},
            },
            "shape": "binary",
        },
        "polymarket": {
            "id": "616906",
            "title": "Will the Fed cut rates 4 times in 2026?",
            "anchor": {"subject": "US federal funds rate", "subject_type": "rate"},
            "occurrence": {
                "type": "cumulative",
                "interval": {"start": "2026-01-01", "end": "2026-12-31"},
            },
            "measurement": {
                "metric": "count_of_25bp_cuts",
                "threshold": {"op": "eq", "value": 4},
            },
            "shape": "binary",
        },
        "expected": "UNRELATED",
    },
]


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY (or add it to .env)")

    print(f"Running {len(_TEST_PAIRS)} test pairs...\n")
    passed = 0
    for pair in _TEST_PAIRS:
        print(f"Pair: {pair['name']}")
        label = judge_pair(pair["kalshi"], pair["polymarket"])
        ok = label == pair["expected"]
        if ok:
            passed += 1
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] got={label}  expected={pair['expected']}\n")

    print(f"Result: {passed}/{len(_TEST_PAIRS)} passed")
    print("(cache_write>0 on call 1, cache_read>0 on calls 2-3 means caching is working)")
