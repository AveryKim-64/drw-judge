#!/usr/bin/env python3
"""Generate candidate (Kalshi, Polymarket) pairs for LLM judging via token overlap.

Avoids O(n×m) LLM calls by first filtering to pairs that share enough
significant keywords. Only those candidates are passed to batch_judge.py.

USAGE
-----
    python3 candidate_pairs.py                    # auto-loads all snapshots
    python3 candidate_pairs.py \\
        --kalshi data/snapshots/kalshi/foo.json \\
        --polymarket data/snapshots/polymarket/bar.json
    python3 candidate_pairs.py --min-overlap 3 --out data/candidates.jsonl
    python3 candidate_pairs.py --stdout           # print pairs, no file write

OUTPUT
------
JSONL — one candidate pair per line:
{
  "kalshi_ticker":       "KXFED-27APR-T4.25",
  "polymarket_id":       "616906",
  "overlap_score":       4,
  "shared_tokens":       ["2027", "apr", "fed", "rate"],
  "kalshi_title":        "Will the upper bound of the federal funds rate ...",
  "polymarket_question": "Will 4 Fed rate cuts happen in 2026?"
}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots"
DEFAULT_OUT = REPO_ROOT / "data" / "candidates.jsonl"
DEFAULT_MIN_OVERLAP = 2

# Words that appear everywhere and carry no discriminative signal
_STOPWORDS = {
    "the", "a", "an", "is", "in", "of", "at", "by", "will", "be",
    "to", "for", "from", "that", "this", "with", "its", "on", "or",
    "and", "are", "was", "were", "has", "have", "had", "not", "but",
    "if", "do", "did", "does", "can", "may", "might", "would", "could",
    "should", "than", "then", "any", "all", "each", "per", "as", "up",
    "it", "he", "she", "they", "we", "you", "who", "what", "when",
    "where", "how", "which", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "following", "according",
    "official", "market", "markets", "resolve", "resolves", "resolution",
    "whether", "after", "before", "between", "during", "next", "last",
    "new", "end", "time", "times", "based", "level", "published",
}

# Normalize common plural/variant forms so Kalshi and Polymarket titles match
_SYNONYMS: dict[str, str] = {
    "rates": "rate",
    "cuts": "cut",
    "hikes": "hike",
    "pauses": "pause",
    "meetings": "meeting",
    "decisions": "decision",
    "points": "point",
    "bounds": "bound",
    "january": "jan",
    "february": "feb",
    "march": "mar",
    "april": "apr",
    "june": "jun",
    "july": "jul",
    "august": "aug",
    "september": "sep",
    "october": "oct",
    "november": "nov",
    "december": "dec",
}

# Pattern for Kalshi date codes embedded in event_ticker (e.g. "27APR" → 2027, "apr")
_DATE_CODE = re.compile(r"(\d{2})([A-Z]{3})")


def _tokens(text: str) -> set[str]:
    """Return significant lowercase tokens from a text string."""
    text = text.lower()
    text = re.sub(r"[^\w\s.%]", " ", text)  # keep digits, dots (4.25), percent
    raw = set(text.split())
    out: set[str] = set()
    for t in raw:
        if len(t) < 3 or t in _STOPWORDS:
            continue
        out.add(_SYNONYMS.get(t, t))
    return out


def _tokens_from_kalshi(m: dict) -> set[str]:
    title_text = (m.get("title") or "") + " " + (m.get("yes_sub_title") or "")
    toks = _tokens(title_text)

    # Parse date codes out of event_ticker (e.g. "KXFED-27APR" → "2027", "apr")
    event_ticker = m.get("event_ticker") or m.get("ticker") or ""
    for match in _DATE_CODE.finditer(event_ticker):
        year_short, month_abbr = match.groups()
        toks.add("20" + year_short)          # "27" → "2027"
        toks.add(month_abbr.lower())          # "APR" → "apr"

    return toks


def _tokens_from_polymarket(m: dict) -> set[str]:
    text = (m.get("question") or "") + " " + (m.get("groupItemTitle") or "")
    return _tokens(text)


def load_snapshots(venue: str, paths: list[Path]) -> dict[str, dict]:
    """Load markets from one or more snapshot files, deduplicating by id/ticker."""
    markets: dict[str, dict] = {}
    for path in paths:
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            print(f"  warn: skipping {path.name}: {e}", file=sys.stderr)
            continue

        raw = data.get("markets", data) if isinstance(data, dict) else data
        if not isinstance(raw, list):
            continue

        for m in raw:
            key = m.get("ticker") if venue == "kalshi" else str(m.get("id", ""))
            if key:
                markets[key] = m

    return markets


def _check_age(venue: str, paths: list[Path]) -> None:
    if not paths:
        return
    newest = max(paths, key=lambda p: p.stat().st_mtime)
    age_h = (datetime.now(timezone.utc).timestamp() - newest.stat().st_mtime) / 3600
    if age_h > 24:
        print(
            f"  warn: newest {venue} snapshot is {age_h:.0f}h old ({newest.name})"
            " — re-fetch with fetch_markets.py before labeling gold-set pairs",
            file=sys.stderr,
        )


def generate_pairs(
    kalshi_markets: dict[str, dict],
    poly_markets: dict[str, dict],
    min_overlap: int,
) -> list[dict]:
    kalshi_toks = {t: _tokens_from_kalshi(m) for t, m in kalshi_markets.items()}
    poly_toks = {pid: _tokens_from_polymarket(m) for pid, m in poly_markets.items()}

    pairs: list[dict] = []
    for ticker, ktoks in kalshi_toks.items():
        if not ktoks:
            continue
        km = kalshi_markets[ticker]
        for pid, ptoks in poly_toks.items():
            shared = ktoks & ptoks
            if len(shared) < min_overlap:
                continue
            pm = poly_markets[pid]
            pairs.append({
                "kalshi_ticker": ticker,
                "polymarket_id": pid,
                "overlap_score": len(shared),
                "shared_tokens": sorted(shared),
                "kalshi_title": km.get("title", ""),
                "polymarket_question": pm.get("question", ""),
            })

    pairs.sort(key=lambda p: p["overlap_score"], reverse=True)
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate candidate Kalshi×Polymarket pairs via token overlap."
    )
    ap.add_argument(
        "--kalshi", type=Path, metavar="PATH",
        help="Kalshi snapshot file (default: all files in data/snapshots/kalshi/)",
    )
    ap.add_argument(
        "--polymarket", type=Path, metavar="PATH",
        help="Polymarket snapshot file (default: all files in data/snapshots/polymarket/)",
    )
    ap.add_argument(
        "--min-overlap", type=int, default=DEFAULT_MIN_OVERLAP, metavar="N",
        help=f"Minimum shared tokens to emit a pair (default: {DEFAULT_MIN_OVERLAP})",
    )
    ap.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, metavar="PATH",
        help=f"Output JSONL path (default: {DEFAULT_OUT})",
    )
    ap.add_argument(
        "--stdout", action="store_true",
        help="Print pairs to stdout instead of writing a file",
    )
    args = ap.parse_args()

    kalshi_paths = (
        [args.kalshi] if args.kalshi
        else sorted((SNAPSHOT_DIR / "kalshi").glob("*.json"))
    )
    poly_paths = (
        [args.polymarket] if args.polymarket
        else sorted((SNAPSHOT_DIR / "polymarket").glob("*.json"))
    )

    _check_age("kalshi", kalshi_paths)
    _check_age("polymarket", poly_paths)

    kalshi_markets = load_snapshots("kalshi", kalshi_paths)
    poly_markets = load_snapshots("polymarket", poly_paths)

    print(
        f"Loaded {len(kalshi_markets)} Kalshi + {len(poly_markets)} Polymarket contracts",
        file=sys.stderr,
    )

    pairs = generate_pairs(kalshi_markets, poly_markets, args.min_overlap)

    print(
        f"Generated {len(pairs)} candidate pairs (min_overlap={args.min_overlap})",
        file=sys.stderr,
    )

    if args.stdout:
        for p in pairs:
            print(json.dumps(p))
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        print(f"Wrote {len(pairs)} pairs → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
