#!/usr/bin/env python3
"""Compare a Kalshi market and a Polymarket market side-by-side.

Automates the pairwise analysis that went into docs/project_brief.md's
Fed test case. Fetches both, maps the Kalshi side into canonical form
(structural parse), prints Polymarket's raw fields, and emits a draft
match label + rationale for human verification.

Polymarket → canonical is deliberately NOT implemented here — that step
requires LLM extraction from free-text `description`, and the prompt
will churn as we learn which fields are load-bearing. Wire it in once
we have a labeled pair dataset to validate against.

The draft label is a conservative hint for a human labeller. Do not
treat it as a match decision.

QUICK START
-----------
    python3 compare_pair.py KXFED-27JAN-T4.25 616906
    python3 compare_pair.py KXFEDDECISION-27DEC-C25 616906 --json

SEE ALSO
--------
    canonical.py           — schema
    fetch_markets.py       — bulk fetch + caching
    docs/project_brief.md  — project context
"""

import argparse
import json
import re
import sys
from datetime import datetime

import requests

from canonical import (
    Anchor, CanonicalContract, MatchLabel, Measurement, Occurrence,
    OccurrenceType, OutcomeType, Raw, Resolution, Shape, Source,
    SubjectType, Threshold, ThresholdOp,
)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_BASE = "https://gamma-api.polymarket.com"

STOPWORDS = set(
    "a an the of in on at to for by with will be is are was were after before "
    "and or not if then than from into it its this that these those".split()
)


# --- fetch ---

def fetch_kalshi_market(ticker):
    r = requests.get(f"{KALSHI_BASE}/markets/{ticker}", timeout=30)
    if r.status_code == 404:
        sys.exit(f"Kalshi market not found: {ticker}")
    r.raise_for_status()
    return r.json().get("market") or {}


def fetch_polymarket_market(market_id):
    r = requests.get(f"{POLY_BASE}/markets/{market_id}", timeout=30)
    if r.status_code == 404:
        sys.exit(f"Polymarket market not found: {market_id}")
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, dict) else {}


# --- Kalshi → canonical (structural) ---

def kalshi_to_canonical(m):
    rules = m.get("rules_primary") or ""
    title = m.get("title") or ""
    yes_sub = m.get("yes_sub_title") or ""
    event_ticker = m.get("event_ticker")
    close_time = m.get("close_time") or ""

    subject_type = _derive_subject_type(rules, title)
    subject = _derive_subject(rules, title)
    jurisdiction = _derive_jurisdiction(rules, title)
    metric = _derive_metric(rules, title)
    threshold = _threshold_from_strike(
        m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike")
    )

    return CanonicalContract(
        source=Source(
            venue="kalshi",
            id=m.get("ticker", ""),
            url=f"https://kalshi.com/markets/{(event_ticker or '').lower()}",
        ),
        anchor=Anchor(subject=subject, subject_type=subject_type, jurisdiction=jurisdiction),
        occurrence=Occurrence(type=OccurrenceType.POINT, point_time=close_time),
        measurement=Measurement(metric=metric, threshold=threshold, includes_unscheduled=False),
        shape=Shape(outcome_type=OutcomeType.BINARY, outcomes=["Yes", "No"], mece_group=event_ticker),
        resolution=Resolution(
            authority=_first_sentence(rules) or "Kalshi settlement",
            trading_cutoff=close_time,
            evaluation_time=m.get("latest_expiration_time") or close_time,
        ),
        raw=Raw(title=title or yes_sub, description=m.get("subtitle", "") or "", rules=rules),
    )


def _threshold_from_strike(strike_type, floor, cap):
    if strike_type == "greater" and floor is not None:
        return Threshold(op=ThresholdOp.GT, value=float(floor))
    if strike_type == "less" and cap is not None:
        return Threshold(op=ThresholdOp.LT, value=float(cap))
    if strike_type == "between" and floor is not None and cap is not None:
        return Threshold(op=ThresholdOp.BETWEEN, value=[float(floor), float(cap)])
    if strike_type in ("structured", "custom"):
        return Threshold(op=ThresholdOp.IS, value="custom")
    return Threshold(op=ThresholdOp.IS, value=strike_type or "unknown")


def _derive_metric(rules, title):
    blob = f"{rules} {title}".lower()
    if "federal funds" in blob or "fed funds" in blob:
        return "target_rate_upper_bound"
    if "hike" in blob or "cut" in blob:
        return "change_in_target_rate_bps"
    if "bitcoin" in blob or "btc" in blob:
        return "btc_price_usd"
    return "resolved_value"


def _derive_subject_type(rules, title):
    blob = f"{rules} {title}".lower()
    if "rate" in blob and ("fed" in blob or "federal" in blob):
        return SubjectType.RATE
    if "price" in blob:
        return SubjectType.PRICE
    if "winner" in blob or "election" in blob or "wins " in blob:
        return SubjectType.WINNER
    return SubjectType.OUTCOME


def _derive_subject(rules, title):
    blob = f"{rules} {title}".lower()
    if "federal funds" in blob or "fed funds" in blob:
        return "US federal funds rate"
    return title.strip() or "unknown"


def _derive_jurisdiction(rules, title):
    blob = f"{rules} {title}".lower()
    if "federal reserve" in blob or "fomc" in blob:
        return "FOMC"
    return None


def _first_sentence(text):
    text = (text or "").strip()
    if not text:
        return None
    m = re.search(r"[.!?]", text)
    return text[: m.start() + 1] if m else text


# --- draft label ---

def _tokens(s):
    return {
        w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
        if w not in STOPWORDS and len(w) > 2
    }


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def draft_label(kalshi_canon, poly_raw):
    k_text = " ".join([kalshi_canon.raw.title, kalshi_canon.anchor.subject, kalshi_canon.raw.rules[:300]])
    p_text = " ".join([poly_raw.get("question", ""), (poly_raw.get("description", "") or "")[:500]])
    k_tokens, p_tokens = _tokens(k_text), _tokens(p_text)
    overlap = len(k_tokens & p_tokens)
    union = len(k_tokens | p_tokens) or 1
    jaccard = overlap / union

    k_dt = _parse_iso(kalshi_canon.resolution.trading_cutoff)
    p_dt = _parse_iso(poly_raw.get("endDate"))
    delta_days = abs((k_dt - p_dt).days) if (k_dt and p_dt) else None

    reasons = [
        f"token jaccard = {jaccard:.2f} ({overlap} shared of {union} non-stopword tokens)",
    ]
    if delta_days is not None:
        reasons.append(
            f"cutoff Δ = {delta_days} days (Kalshi {k_dt.date()}, Polymarket {p_dt.date()})"
        )
    else:
        reasons.append("cutoff Δ unavailable (one or both dates missing/unparseable)")

    # Conservative heuristic — hint only.
    if jaccard < 0.08:
        return MatchLabel.UNRELATED, reasons + ["low token overlap → subjects likely differ"]
    if delta_days is not None and delta_days > 60 and jaccard >= 0.15:
        return MatchLabel.SYNTHETIC, reasons + [
            "large date gap but subjects overlap → likely different temporal scope "
            "(Polymarket cumulative/interval vs Kalshi point) → synthetic candidate, "
            "verify by checking whether Polymarket resolves on a basket of Kalshi meetings",
        ]
    if delta_days is not None and delta_days <= 2 and jaccard >= 0.35:
        return MatchLabel.EQUIVALENT, reasons + [
            "cutoffs align and subjects overlap strongly → possibly equivalent, verify resolution criteria",
        ]
    return MatchLabel.SYNTHETIC, reasons + ["mixed signals — flag for human review"]


# --- output ---

def _truncate(s, n=200):
    s = s or ""
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _print_report(kalshi, poly, canon, label, reasons):
    bar = "=" * 78
    print(bar, "KALSHI", bar, sep="\n")
    print(f"  ticker       : {kalshi.get('ticker')}")
    print(f"  title        : {kalshi.get('title')}")
    print(f"  yes_sub_title: {kalshi.get('yes_sub_title')}")
    print(f"  close_time   : {kalshi.get('close_time')}")
    print(f"  strike_type  : {kalshi.get('strike_type')}")
    print(f"  floor_strike : {kalshi.get('floor_strike')}")
    print(f"  cap_strike   : {kalshi.get('cap_strike')}")
    print(f"  event_ticker : {kalshi.get('event_ticker')}  (MECE group)")
    print(f"  rules_primary: {_truncate(kalshi.get('rules_primary', ''), 300)}")
    print("\n  CANONICAL (structural parse):")
    for line in canon.to_json().splitlines():
        print("    " + line)

    print("\n" + bar, "POLYMARKET", bar, sep="\n")
    print(f"  id           : {poly.get('id')}")
    print(f"  question     : {poly.get('question')}")
    print(f"  endDate      : {poly.get('endDate')}")
    print(f"  outcomes     : {poly.get('outcomes')}")
    print(f"  outcomePrices: {poly.get('outcomePrices')}")
    print(f"  resolutionSrc: {poly.get('resolutionSource') or '(empty — authority lives in description)'}")
    print(f"  description  : {_truncate(poly.get('description', ''), 400)}")
    print("\n  CANONICAL    : (pending — requires LLM extraction from description)")

    print("\n" + bar)
    print(f"DRAFT LABEL: {label.value}")
    print(bar)
    for r in reasons:
        print(f"  - {r}")
    print("  (draft label is a conservative hint — verify with a human)")


def main():
    ap = argparse.ArgumentParser(
        description="Compare a Kalshi ticker and Polymarket id side-by-side; emit a draft match label.",
    )
    ap.add_argument("kalshi_ticker", help="Kalshi market ticker, e.g. KXFED-27JAN-T4.25")
    ap.add_argument("polymarket_id", help="Polymarket market id (numeric string)")
    ap.add_argument("--json", action="store_true", help="Emit structured JSON instead of a printed report")
    args = ap.parse_args()

    kalshi = fetch_kalshi_market(args.kalshi_ticker)
    poly = fetch_polymarket_market(args.polymarket_id)
    canon = kalshi_to_canonical(kalshi)
    label, reasons = draft_label(canon, poly)

    if args.json:
        print(json.dumps({
            "kalshi_raw": kalshi,
            "polymarket_raw": poly,
            "kalshi_canonical": canon.to_dict(),
            "draft_label": label.value,
            "rationale": reasons,
        }, indent=2, default=str))
    else:
        _print_report(kalshi, poly, canon, label, reasons)


if __name__ == "__main__":
    main()
