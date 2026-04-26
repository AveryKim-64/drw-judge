# Project brief: cross-venue prediction market matching engine

**For a collaborator joining the project.** Start here.

The brief is organized so you can read top-to-bottom and never need to skip ahead:

1. **What we're building** — the 30-second pitch.
2. **Goal** — the labels we emit.
3. **Why it's actually hard** — the traps a naive matcher falls into.
4. **Worked example** — Fed rate contracts, which motivated our current design.
5. **Canonical schema** — how we represent a contract internally.
6. **Delivering to the arb team** — what labels are worth in dollars and what we have to hand over.
7. **Next steps for Avery** — the ordered work plan.
8. **Background reading** — the AIA Forecaster paper.
9. **Sources** — quick-lookup table of everything in the repo.
10. **Using Claude Code in this repo** — tool tips.

---

## 1. The 30-second version

**Prediction markets** are exchanges where people trade contracts that pay $1 if a real-world event happens and $0 otherwise — "Will the Fed cut rates in June?", "Will Team X win the Super Bowl?". The two biggest U.S.-facing venues are:

- **Kalshi** — regulated, event contracts, lots of structured economic questions.
- **Polymarket** — crypto-native, broader topic range, questions often written in free-form prose.

When both venues list "the same" question, their prices can drift apart. If you can reliably recognize that Kalshi contract X and Polymarket contract Y pay out under identical conditions, you can **arbitrage** them: buy the cheaper side, sell the richer side, and pocket the spread when they converge at resolution.

**Our job** is the recognizer. We're building the piece that says "these two contracts resolve to the same outcome" (or don't). A separate team downstream uses that signal to actually trade. We're infrastructure; they're the P&L.

"Same outcome" is harder than it looks — the rest of this doc explains why, what we output, and how to move the project forward.

## 2. Goal

Build a **matching engine**: given a contract on one venue, find the contract(s) on the other venue that resolve equivalently, and label the relationship.

For each cross-venue pair of contracts, the engine emits one of:

- `equivalent` — same underlying event, same resolution rules, same cutoff time. Safe to treat as one book.
- `subset` / `superset` — one contract pays out on a strict subset of the outcomes the other does. (Example: "Fed cuts 25bps in June" is a subset of "Fed cuts at all in June".)
- `synthetic` — you can reconstruct one contract by combining several contracts from the other venue. More on this below.
- `unrelated` — no useful relationship.

Why five labels and not just equivalent/not-equivalent? Because traders downstream treat each case very differently — `equivalent` is a direct arb, `synthetic` is a basket trade that needs a pricing model, `subset` is relative-value. Section 6 works through what each label is worth.

## 3. Why this is actually hard

It's tempting to think "just compare the titles." Three traps kill that approach:

1. **Scope.** "Fed cuts in 2026" and "Fed cuts at the June meeting" both mention Fed cuts in 2026, but one is annual and one is a single meeting.
2. **Thresholds.** "Bitcoin above $120k by year-end" and "Bitcoin above $125k by year-end" look almost identical to an embedding model. They are not the same contract.
3. **Resolution details.** Two "will X happen" contracts can differ on *who decides*, *when the cutoff is*, and *whether unscheduled events count*. These are often buried in paragraphs of legal prose, not title text.

The engine's value comes from getting these right consistently. A naive matcher that gets them wrong doesn't just fail silently — it hands the arb team a mismatched pair, which becomes a losing trade.

## 4. Worked example: Fed rate decisions

This is the case that motivated our current design. Pulled live on 2026-04-21. We grabbed the highest-volume Fed-related contract on each venue and tried to match them.

**Polymarket 616906** — "Will 4 Fed rate cuts happen in 2026?"
- $986k volume, ends 2026-12-31.
- Resolves YES if the Fed makes at least 4 cuts of 25bps (0.25%) over the whole year.
- **Counts emergency cuts** (unscheduled meetings).
- Single binary yes/no outcome.

**Kalshi `KXFEDDECISION-*`** — one event per FOMC meeting (the scheduled Fed meetings).
- Each meeting has **five mutually exclusive outcomes**: big cut (`C26`, >25bps), small cut (`C25`), hold (`H0`), small hike (`H25`), big hike (`H26`).
- Point-in-time: the question is "what happened at *this* meeting?"
- **Emergency cuts not counted** — only scheduled FOMC meetings resolve this contract.

**Verdict: `synthetic`, not `equivalent`.**

These aren't the same contract, but the Polymarket one is *reconstructable* from a basket of Kalshi contracts. If you know the Kalshi prices for each scheduled 2026 FOMC meeting, you can compute what the fair price of Polymarket's "4+ cuts this year" should be — it's a function of the paths through all those meetings.

But a naive matcher that just sees "Fed / cut / 2026" on both sides would call them near-duplicates. That's the mislabel that costs money: when the per-meeting Kalshi price moves but the annual-cumulative Polymarket price doesn't (or vice versa), a system treating them as "the same" will trade wrongly.

**What this example pinned down:** two fields in our schema are load-bearing and can't be skipped:
- `occurrence.type` — is this a point event, an interval, a cumulative count, or a sequence?
- `includes_unscheduled` — do off-schedule events count?

Neither is derivable from the title. Polymarket's version requires an LLM to extract them from free-text `description` prose — Kalshi gives them to us structurally.

## 5. The canonical schema

To compare contracts across venues, we map both sides into a shared structure (`canonical.py`):

```
CanonicalContract {
  source:      { venue, id, url }
  anchor:      { subject, subject_type, jurisdiction }               // WHAT is this about
  occurrence:  { type: point|interval|cumulative|sequence, ... }     // WHEN does it resolve
  measurement: { metric, unit, threshold, includes_unscheduled }     // HOW is it measured
  shape:       { outcome_type, outcomes, mece_group }                // yes/no or multi-outcome
  resolution:  { authority, data_source, trading_cutoff, evaluation_time }
  raw:         { title, description, rules }
}
```

`mece_group` is the ID of a set of outcomes that are **mutually exclusive and collectively exhaustive** — exactly one will happen, and the group's probabilities sum to 1. Kalshi's "five possible FOMC outcomes" is a MECE group. Recognizing MECE groups is how we detect `synthetic` relationships.

Matching runs in **two stages**:

1. **Candidate retrieval.** Embed a short string (like `f"{subject} | {occurrence.type} | {metric}"`) for every contract and find top-k nearest neighbors across venues. This is cheap and lets us avoid O(N²) LLM calls.
2. **Verification.** For each candidate pair, hand the full canonical form of both contracts to an LLM and ask for a label.

**One key asymmetry to keep in mind:**
- **Kalshi → canonical** is mostly mechanical. Kalshi returns rich, typed fields (`floor_strike`, `strike_type`, `rules_primary`), so we can parse rather than interpret.
- **Polymarket → canonical** is mostly **LLM extraction from prose.** Most of the information we need lives in the free-text `description`. This is the highest-variance part of the pipeline — we validate every prompt/model change against a hand-labeled set before shipping.

## 6. Delivering to the arb team (how our output becomes money)

This project doesn't make money directly. A separate team turns our match labels into trades. Our contribution to the firm's P&L is **a matcher the arb team trusts enough to size positions against.**

### Three things the arb team grades us on

- **Precision** — when we say `equivalent`, we're right. False positives lose real money: the arb team takes both legs thinking they cancel, then one resolves differently and the loss is uncapped.
- **Coverage** — we find the matches that exist. A false negative is an opportunity missed, not a loss, but missed opportunities are why the desk is paying for this.
- **Latency** — fresh enough that signals are actionable before the spread closes.

Of the three, **precision matters most first.** A low-coverage, high-precision matcher is useful on day one. A high-coverage, low-precision matcher is worse than nothing — it actively loses money.

### How each label becomes a trade

Venue-mechanics refresher (relevant to every label below):

- **Kalshi** — central limit order book. YES and NO are separate tradable sides; "selling YES" is done by buying NO at `1 − YES_bid`. Per-contract payout is $1 at resolution. Fees are a few cents per $100 notional.
- **Polymarket** — on-chain AMM + order book on Polygon. USDC-denominated; same $1-at-resolution payout structure. Fees embedded in the spread; gas is usually a few cents.

Cross-venue "arb" means a position that locks in P&L regardless of which way the event resolves. What "lock in" means depends on the label:

#### `equivalent` → pure two-leg arb

Both contracts pay out identically on every scenario. If prices disagree, buy the cheap side, sell (via the opposite leg on that venue) the rich side.

Worked example — suppose our matcher labels a pair `equivalent`, and the market shows:

- Polymarket YES trades at **0.55**
- Kalshi YES trades at **0.60** (equivalently, Kalshi NO trades at **0.40**)

Trade:
- Buy 1 Polymarket YES for **0.55**
- Buy 1 Kalshi NO for **0.40** (this is the "sell Kalshi YES" leg)
- Total outlay: **0.95**

At resolution, exactly one of these pays $1 (the two contracts are equivalent, so they resolve together — "Poly YES + Kalshi NO" covers both scenarios). Guaranteed payoff of $1 for $0.95 outlay = **5¢ of arb per unit** minus fees and cost of capital.

**Where this goes wrong if our label is wrong:** if the pair is actually `synthetic` or `subset`, there exist resolution scenarios where both legs pay 0 or both pay $1, and the "guarantee" fails. That's why false-positive `equivalent` labels are the costliest mistake.

#### `subset` / `superset` → relative-value trade, not guaranteed arb

Kalshi ⊂ Polymarket means Kalshi-YES implies Poly-YES but not vice versa. The prices should satisfy `Poly(YES) ≥ Kalshi(YES)` at all times — if that inequality inverts, it *is* a pure arb (buy Poly YES cheap, sell Kalshi YES rich). When the inequality holds but the spread is unusually wide or tight relative to history, the arb team can take a view, but it's directional, not risk-free.

Our responsibility for these labels: tell the arb team **which way the subset relation runs** (Kalshi ⊂ Poly vs. Poly ⊂ Kalshi), not just the unsigned label.

#### `synthetic` → basket arb

One contract is reconstructable from the MECE basket on the other venue. Example: Polymarket "4+ cuts in 2026" is a function of Kalshi's per-meeting outcomes across all 2026 FOMC meetings.

The trade is a **basket**: replicate the Polymarket payoff using a weighted portfolio of Kalshi legs, then take the opposite side of Polymarket. When Polymarket trades away from the basket-implied fair value, the arb team buys the cheap side and holds the replicating basket on the other.

This is substantially harder to execute than `equivalent` arb:
- Requires a **pricing model** — the basket's value depends on the joint distribution of Kalshi outcomes, which aren't independent (a cut in June raises the probability of a cut in July, etc.). Getting correlation wrong is expensive.
- Requires simultaneous fills across multiple Kalshi legs; partial fills leave unhedged exposure.
- Higher capital requirement and higher fee drag.

Our responsibility for `synthetic` labels: hand the arb team the **explicit basket recipe**, not just the word "synthetic". Which Kalshi markets combine how to replicate the Polymarket payoff? Without that, they're redoing our work.

#### `unrelated` → no trade. Don't even surface.

#### `ambiguous` / low-confidence → do not trade. Queue for human review.

### The handoff record

A label string alone isn't enough. Each match we publish should be a typed record roughly like:

```
{
  "match_id":        "m-20260421-0042",
  "label":           "synthetic",
  "confidence":      0.87,                      // from the LLM verifier
  "direction":       null,                      // "kalshi_subset_of_poly" for subset/superset
  "kalshi":          { ...canonical form... },
  "polymarket":      { ...canonical form... },
  "synthetic_basket": {                         // present only for synthetic
    "replicates":    "polymarket:616906",
    "legs": [
      { "kalshi_ticker": "KXFEDDECISION-26APR-C25", "weight_fn": "..." },
      ...
    ]
  },
  "caveats":         ["includes_unscheduled mismatch", "cutoff delta = 23min"],
  "produced_at":     "2026-04-21T18:30:00Z",
  "matcher_version": "v0.4.2"
}
```

Notes on each field:

- **`confidence`** — the arb team will threshold on this to size positions. A 0.55-confidence `equivalent` label is not the same product as a 0.95-confidence one. Surface it; don't hide it behind a binary decision.
- **`direction`** — mandatory for `subset`/`superset`. Missing direction = unusable label.
- **`synthetic_basket`** — the replication recipe. Without it, a `synthetic` label is just a warning, not a trade signal.
- **`caveats`** — explicit callouts of known asymmetries (emergency-cut treatment, cutoff gap, authority difference). The arb team decides whether a given caveat is tradable; our job is to surface it.
- **`matcher_version`** — so they can invalidate a match when we ship a model change that would have relabeled it.

### Freshness and invalidation

Arb opportunities compress as prices converge, so our output is a live feed, not a batch report:

- New markets listing on either venue should trigger match attempts within minutes, not hours. Stale coverage = missed arb.
- Material changes to a contract (rules edited, cutoff revised, resolution source changed) should **invalidate** the existing match and re-run — the old label may no longer apply.
- When our matcher version bumps, old matches should be flagged as "stale-until-relabeled" so the arb team knows whether they're acting on the current logic.

We don't need to solve live feed integration this summer — but the output schema should be designed so the arb team could subscribe to a stream of these records without additional transformation.

### What's out of scope for this project

Explicitly *not* our problem — these belong to the arb team or infra:

- Live order-book feeds, execution, position sizing, capital management.
- Risk limits, kill switches, latency SLAs on trade execution.
- Cross-venue settlement mechanics and fees.
- The pricing model for `synthetic` baskets (joint-distribution assumptions, correlation estimation). We hand them the basket *structure*; they model the *prices*.

We emit labels and basket structures. They model prices and trade. Keep that boundary clean — but make the handoff record rich enough that they don't have to re-derive anything we already know.

## 7. Next steps for Avery

### The blocker right now: evaluation

We cannot report precision or recall because we have no labeled dataset. That's the first thing to fix. Without it:

- We can't measure whether changes to prompts, embeddings, or the LLM model are improvements or regressions.
- The arb team has no basis to decide how much capital to allocate against our signals.
- Every conversation about "is this matcher good enough?" is vibes.

Until a gold set exists, everything else is building on sand.

### Recommended sequence (ordered by impact)

**1. Build a labeled gold set on one vertical first — target ~200 pairs on Fed-rate contracts.**

Fed contracts are the right starting vertical: high volume (real money at stake), we already understand the traps (the worked example in §4), and both venues list them heavily. Scope:

- ~200 hand-labeled Kalshi–Polymarket pairs covering `equivalent`, `subset`/`superset`, `synthetic`, `unrelated`.
- **Deliberate hard negatives**, not just random pairs. Categories to include:
  - *Scope traps* — annual vs. per-meeting (the example we already worked).
  - *Threshold traps* — "Fed cuts 25bps" vs. "Fed cuts ≥25bps" vs. "Fed cuts by any amount".
  - *Resolution-authority traps* — both contracts reference "the Fed decision" but resolve on different data sources or cutoff times.
  - *Unscheduled-event traps* — one contract counts emergency cuts, the other doesn't.
- Label format: pair ID + label + one-sentence rationale. The rationale is for human review later and for prompting examples.
- Avery does the hand-labeling. Claude can *draft* candidate pairs and proposed labels, but the gold set has to be human-signed-off, otherwise we're grading our own homework.

Why ~200 and not 1000: enough to measure precision on each label class meaningfully, small enough to finish in a reasonable window. Scale later.

**Status (as of 2026-04-21):** an initial batch of 25 candidate pairs is in the repo at `data/gold_set/fed_v1.jsonl`, all `UNLABELED`. Trap coverage: 6× `scope_trap`, 6× `threshold_trap`, 4× `threshold_trap_rate`, 3× `sequence_trap`, 3× `temporal_trap`, 2× `unrelated_control`, 1× `unscheduled_trap`. Each row has a `hypothesis` field explaining what the pair was picked to stress. Labeling instructions, label definitions, and quality bar are in `docs/labeling_guide.md` — read that before starting. After v1 is labeled and reviewed, we expand toward the ~200 target.

**Success criterion:** `data/gold_set/fed_v1.jsonl` fully labeled (every row has `label`, `rationale`, `labeled_by`, `labeled_at`), with a balanced distribution across the five `MatchLabel` values. Downstream tooling can load the JSONL directly as ground truth.

**2. Finish Polymarket → canonical extraction (currently stubbed in `compare_pair.py`).**

Today `compare_pair.py` parses Kalshi structurally and leaves Polymarket mostly as raw prose. Until both sides canonicalize, we can't compare `occurrence.type` across venues, which means we can't detect `synthetic` relationships — the exact thing §4 showed we need.

Concretely: an LLM prompt that takes Polymarket's `description` and returns the canonical fields (`occurrence.type`, `includes_unscheduled`, `measurement.threshold`, `resolution.authority`, etc.). Validate each prompt/model change against the gold set from step 1. This is why the gold set comes first.

**Success criterion:** running `compare_pair.py` on the gold-set pairs produces canonical forms for both sides, and the draft labels agree with the hand labels on ≥80% of pairs (ballpark — we'll see what's achievable).

**3. Build a candidate retriever so matching is tractable across all contracts.**

With ~200 gold pairs we can do O(N²) brute force. Across thousands of live contracts per venue, we can't. The retriever narrows candidates before the expensive LLM verifier runs. Embed a short canonical summary string (`f"{subject} | {occurrence.type} | {metric}"`) and use cosine similarity to pull top-k nearest neighbors across venues.

**Success criterion:** for every pair in the gold set where the ground-truth match exists, the correct counterpart appears in the top-k (target k=10, then tighten). This is a **recall-at-k** measurement we can report.

**4. Wire up the LLM verifier and measure end-to-end.**

Given a candidate pair from the retriever, hand both canonical forms to an LLM and ask for a label + rationale. Measure precision and recall on the gold set. Iterate on the prompt. This is the first point where we can tell the arb team a real number.

**Success criterion:** a precision number per label class, and a recall number, on held-out gold-set pairs. Whatever those numbers are, they're better than "we don't know."

**5. Expand to a second vertical.**

Once the Fed loop works end-to-end, pick a second contract class (BTC price levels, election contracts, or macro — volume on the Dune dashboard should guide this) and repeat. The second vertical tests whether the pipeline generalizes or whether we over-fit to Fed.

### What not to do first

- **Don't build the fancy retriever before the gold set.** Without ground truth you can't tell if it's working.
- **Don't chase coverage across all verticals simultaneously.** One well-characterized vertical beats four shallow ones for this stage.
- **Don't try to automate labeling with an LLM to save time.** The whole point of a gold set is that a human signed off. A gold set written by an LLM is just a consistency test for that LLM.

## 8. Background reading: the AIA Forecaster paper (Bridgewater AIA Labs)

Given to Avery as onboarding reading. It's a *forecasting* paper — how to get LLMs to make good probabilistic predictions about future events — not a matching paper. But three ideas transfer directly to what we're building:

1. **Agentic search.** Instead of dumping a fixed search result into the LLM, let the LLM decide what to look up and adapt as it reads. Useful for us when a borderline pair requires pulling the actual resolution criteria from an exchange's website. Relevant to step 4 above (the LLM verifier).

2. **Supervisor-agent ensembling.** Run multiple forecasters in parallel, then have a supervisor LLM read all their outputs and reconcile. Our analogue: we'll likely have multiple matchers (a cheap embedding-based one, a structural-rules one, a heavier LLM one) and they'll disagree on hard cases. A supervisor can arbitrate.

3. **Ensembling beats either input alone.** This is the most important finding for us. The paper's forecaster *loses* to market consensus on liquid markets — markets are pretty good! But **forecaster + market combined beats market alone**, because the forecaster carries information the market hasn't priced in. That's exactly the arbitrage thesis: if our matcher spots an equivalence that the market hasn't recognized, the price gap between the two venues is real edge.

Full paper at `docs/2511.07678v1.md`.

## 9. Sources

| What | Where |
|---|---|
| Paper (markdown, easier for Q&A) | `docs/2511.07678v1.md` |
| Paper (original PDF) | `docs/2511.07678v1.pdf` |
| Prediction-market landscape overview | `docs/dune_dashboard_notes.md` + https://dune.com/datadashboards/prediction-markets |
| Dune queries (for live volume/OI numbers) | `getDune.py <query_id>` — see CLAUDE.md; needs a *query ID*, not a dashboard URL |
| Kalshi public API | `https://api.elections.kalshi.com/trade-api/v2/markets` and `/events` |
| Polymarket public API | `https://gamma-api.polymarket.com/markets` |
| Kalshi API docs | https://docs.kalshi.com/api-reference/ |
| Polymarket API docs | https://docs.polymarket.com/api-reference/ |
| Fetcher / local cache | `fetch_markets.py` — unified CLI over both venues; snapshots land in `data/snapshots/` |
| Canonical schema | `canonical.py` — the shared data structure both venues get mapped into |
| Pair comparison tool | `compare_pair.py <kalshi-ticker> <polymarket-id>` — side-by-side view + draft label |
| Gold-set candidate pairs (Fed v1) | `data/gold_set/fed_v1.jsonl` — 25 hand-picked Kalshi × Polymarket pairs stressing specific trap categories, awaiting human labels |
| Labeling guide | `docs/labeling_guide.md` — label definitions, workflow, and quality bar for the gold set |
| Cached market snapshots | `data/snapshots/kalshi/` and `data/snapshots/polymarket/` — raw JSON from `fetch_markets.py`, organized by venue + timestamp |

## 10. Using Claude Code in this repo

**Orientation.** Claude Code auto-loads `CLAUDE.md` from `/Users/ikim/avery`, so Claude already has the context — no need to paste it in. If a collaborator is new to Claude Code, see the "Coaching Avery on Claude Code" section of `CLAUDE.md` for tips Claude should volunteer in the flow of work.

**Asks that work well:**
- "Pull the top N Fed / Bitcoin / election contracts from Kalshi and Polymarket." Claude will run `fetch_markets.py` (or `curl` directly).
- "Map this Kalshi ticker and this Polymarket id into canonical form and compare." Claude builds `CanonicalContract` objects from `canonical.py` and prints a side-by-side.
- "Propose hard negatives for training — scope traps, threshold traps, resolution-authority traps." Claude generates candidate pairs designed to fool a naive matcher.
- "Cite section X.Y of the paper" — Claude opens `docs/2511.07678v1.md` and quotes by heading.

**Quick-reference commands:**
```
python3 fetch_markets.py kalshi --series KXFED --limit 50
python3 fetch_markets.py kalshi --event KXFEDDECISION-27DEC
python3 fetch_markets.py polymarket --id 616906
python3 fetch_markets.py polymarket --grep "rate cuts" --limit 500
python3 canonical.py     # smoke test; prints a sample CanonicalContract as JSON
python3 compare_pair.py KXFEDDECISION-27DEC-C25 616906   # side-by-side + draft label

# inspect / labeling workflow for the gold set
cat data/gold_set/fed_v1.jsonl | head -5          # peek at pair rows
wc -l data/gold_set/fed_v1.jsonl                   # count total pairs
open docs/labeling_guide.md                        # label definitions + workflow
```

**Live data sources, practical notes:**
- Kalshi and Polymarket public endpoints work with no auth for listing. Claude can `curl` them directly.
- Dune is **blocked for WebFetch**, so use `getDune.py <query_id>` instead. The API needs a specific *query ID* — dashboard URLs point to multi-query pages and there's no "fetch a whole dashboard" endpoint. Don't pass `--execute` unless a fresh run is genuinely needed (free-tier credits are tight).

**Gotchas this project has already hit** (worth knowing before you re-discover them):
- Kalshi `close_time` is typically ~1 minute before the resolving event (e.g., 18:59Z for a 19:00Z FOMC release). Polymarket `endDate` is usually end of the calendar day. They look like the same field. They are not.
- Polymarket `resolutionSource` is often an empty string — the actual resolution authority is buried in the `description` prose.
- Polymarket's API ignores `tag_slug` filters in practice. Pull broadly by volume and filter client-side.
- Most interesting Kalshi economic questions are scalar-threshold events with a MECE group of outcomes. Most Polymarket economic questions are binary-cumulative (one yes/no over a long window). This mismatch is the norm, not the exception — it's *why* the engine needs `occurrence.type` in the schema.

**When Claude will pause and ask before acting:** destructive git operations, anything that runs `getDune.py --execute` (burns credits), scope assumptions about the matching engine if they feel like they're drifting from what Avery has confirmed, and ambiguous design calls where Claude doesn't know whose decision it is.
