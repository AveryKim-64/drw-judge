# Gold-set labeling guide (Fed v1)

This is how to label the ~25 candidate pairs in `data/gold_set/fed_v1.jsonl`. The goal is a hand-verified dataset we can measure the matching engine against. Precision of downstream matchers is only as good as this ground truth, so it's worth being deliberate.

## What the gold set is for

When the matcher eventually emits labels like `equivalent` or `synthetic` on unseen pairs, we compare against this file to compute:

- **Precision** — of pairs the engine called `equivalent`, how many actually are?
- **Recall** — of pairs that truly are `equivalent`, how many did the engine find?
- **Confusion matrix** — which labels does the engine systematically mix up?

No number in any future discussion of matcher quality will be trustworthy unless it traces back to a labeled set like this.

## The file format

`data/gold_set/fed_v1.jsonl` — one JSON object per line. Each row is one cross-venue pair.

Fields to understand:

- `pair_id` — stable identifier. Don't change.
- `kalshi_ticker`, `kalshi_title` — the Kalshi market. Title is a short description, not the full title from the exchange.
- `polymarket_id`, `polymarket_question` — the Polymarket market.
- `candidate_category` — the *trap class* this pair is testing. Tells you what the pair was picked to stress, not the answer.
- `hypothesis` — a short note on what makes this pair interesting. Read this before labeling.
- `label` — starts as `UNLABELED`. Fill it in.
- `rationale` — starts as `null`. Fill it in with a one-sentence explanation.
- `labeled_by`, `labeled_at` — fill with your initials and date (YYYY-MM-DD).

## The label values

Pick exactly one per pair. Definitions:

- **`equivalent`** — the two contracts resolve identically on every scenario. Same event, same outcome condition, same cutoff, same authority, same treatment of edge cases (unscheduled events, etc.). Rare — most pairs won't qualify.
- **`subset`** — Kalshi's YES-set is strictly contained in Polymarket's YES-set. Kalshi YES implies Polymarket YES, but not vice versa. (Example: Kalshi "Fed hikes 25bps at Jun 2026" vs. Polymarket "any hike by Jun 2026 meeting" — Kalshi is a subset of Polymarket.)
- **`superset`** — the reverse: Polymarket's YES-set is strictly contained in Kalshi's YES-set.
- **`synthetic`** — one contract is reconstructable from a combination of contracts in the other's MECE group, but neither is a subset of the other. The canonical case: Polymarket annual-cumulative is synthetic w.r.t. Kalshi per-meeting MECE.
- **`unrelated`** — the contracts measure different things. No useful logical relationship.

**When unsure**, lean `synthetic` over `equivalent`. A false `equivalent` is the costliest mistake (the arb team sizes real money against it); a false `synthetic` is a recoverable bug.

### Rules of thumb

- If the cutoff times are days apart or the years don't match, start with `unrelated` and argue up from there.
- If one side counts emergency / unscheduled events and the other doesn't, they are at best `synthetic`, not `equivalent`. Emergency cuts are rare but they happen (2008, 2020, Mar 2023).
- If one side is cumulative/interval and the other is point-in-time, not `equivalent`. Probably `synthetic`.
- Scope differences (annual vs per-meeting) are almost always `synthetic` or `unrelated`, never `equivalent`.

## How to label a pair (workflow)

For each row:

1. **Read the hypothesis field.** It tells you what the pair was picked to stress. Don't skip it.
2. **Read both contracts in full.** Title alone isn't enough. Pull the full resolution criteria:
   ```
   python3 compare_pair.py <kalshi_ticker> <polymarket_id>
   ```
   This prints both markets side-by-side including Polymarket's `description` prose (which is where the load-bearing details live).
3. **Work through the resolution mechanics.** For each contract, ask: what exact sequence of real-world events makes this resolve YES? NO?
4. **Compare the YES-sets.** If A's YES-set equals B's YES-set → `equivalent`. If one strictly contains the other → `subset`/`superset`. If both are partial windows into a shared MECE decision tree → `synthetic`. If they measure disjoint things → `unrelated`.
5. **Write the rationale in one sentence.** Template: *"Kalshi resolves on {X}; Polymarket resolves on {Y}; relationship is {label} because {the distinguishing fact}."* Future-you should be able to audit the decision from this sentence alone.
6. **Fill `labeled_by` with your initials, `labeled_at` with today's date.**

## Trap categories reference

The candidate pairs deliberately over-sample these categories. Knowing which category a pair belongs to helps you focus on the right distinguishing feature:

| Category | What to look at | Typical label |
|---|---|---|
| `scope_trap` | Is one side cumulative/annual and the other per-meeting? | `synthetic` |
| `sequence_trap` | Is one side a conjunction (cut AND pause AND pause) and the other a single leg? | `synthetic` |
| `temporal_trap` | Do the years / meeting dates match? Often they don't despite similar titles. | `unrelated` |
| `threshold_trap` | Same meeting, different cut size or direction (hike vs cut)? | `subset` / `unrelated` |
| `threshold_trap_rate` | Both reference a rate level but with different operators / bounds (upper vs lower, >= vs <=)? | context-dependent — work through the algebra |
| `unscheduled_trap` | Does one count emergency actions and the other not? | usually `synthetic` |
| `unrelated_control` | Obvious non-match included as a sanity check. | `unrelated` |

## Quality bar

- **Don't label fast.** Fifty quick labels with 20% errors is worse than ten slow labels with zero errors, because downstream we'll tune the engine against these labels. Errors in the gold set turn into calibrated-wrong matchers.
- **If a pair feels ambiguous**, say so — set `label` to `ambiguous` and write what's ambiguous in the rationale. Better than a guess. We'll review those together.
- **If you disagree with the `hypothesis`**, label based on your own reading and flag it in the rationale. The hypothesis is a draft, not a decision.
- **If a pair requires looking up external data** (actual Fed meeting schedules, emergency-cut history) and you can't find it, note that in the rationale and move on. We'll revisit.

## Target completion

All 25 pairs in `fed_v1.jsonl`. Expect ~10-15 minutes per pair if you're reading resolution criteria carefully, so plan for 4-6 hours. Split across sessions if that's more sustainable — the gold set should be your best work, not your fastest.

## What comes after this batch

Once v1 is done and reviewed:

1. We compute label distribution and sanity-check it (if we have 23 `synthetic` and 2 `unrelated`, we have a coverage gap).
2. We use v1 to validate the Polymarket-to-canonical LLM extraction (next step in the project brief).
3. We expand to v2 with a second vertical (BTC price levels or elections).

See `docs/project_brief.md` → "Next steps — how this becomes money" for the full sequence.
