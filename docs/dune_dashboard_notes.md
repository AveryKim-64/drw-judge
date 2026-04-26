# Dune prediction-markets dashboard — notes

**Link:** https://dune.com/datadashboards/prediction-markets
(also published at https://dune.com/dunedata/prediction-markets — same content)

Paired with the AIA Forecaster paper as onboarding background on the prediction-markets landscape.

## What Dune is

[Dune](https://dune.com) is a blockchain analytics platform. Anyone can write SQL queries against indexed on-chain data from Ethereum, Polygon, and other chains, then publish the results as shareable charts and dashboards. Most serious crypto-data dashboards you see floating around online are built on Dune.

That matters for us because **Polymarket runs on Polygon**, so every trade is on-chain and queryable. Dune is the fastest way to get cross-venue volume and user numbers without standing up our own pipeline.

*(Kalshi is a regulated U.S. exchange, not on-chain, so its data gets into Dune via a separate feed — not through on-chain indexing.)*

## What this specific dashboard shows

A weekly breakdown of activity across the four main prediction-market venues:

- **Polymarket** — crypto-native, on Polygon. Largest by volume.
- **Kalshi** — regulated, U.S.-facing. Second, and closing.
- **Limitless** — newer on-chain venue, next tier.
- **Myriad** — newer on-chain venue, next tier.

The metrics covered include:

- **Notional volume** — total dollar value traded.
- **"Normal" volume** — volume filtered to exclude wash-trading-looking activity (same wallet trading with itself to inflate numbers). Cleaner than raw notional for actual liquidity assessment.
- **Open interest (OI)** — total dollar value of contracts still open (not yet resolved or closed out). A good proxy for how much real capital is parked in a venue at a given moment.
- **Total trades** — trade count, regardless of size.
- **Weekly unique users** — engagement / user-base growth signal.

Granularity is weekly. Author is `@dunedata` on Dune.

## Why we care about it

For the matching engine, knowing the **relative size** of each venue tells us where to focus. Rough picture as of early 2026:

- Polymarket + Kalshi together dominate volume. They're where arbitrage dollars live and where our engine should land first.
- Limitless and Myriad are smaller but growing and worth tracking — a venue that grows fast becomes a new arbitrage leg.

Volume also informs **which contracts we prioritize labeling.** A $1M/day contract that mismatches costs real money; a $500/day contract that mismatches basically doesn't matter. Volume is how we rank.

## How to actually get numbers from it

This is the tricky part.

**Don't try to fetch the dashboard URL directly.** Dune returns HTTP 403 to automated requests (including Claude Code's `WebFetch`). Burning a turn on it just fails.

**What works:**

1. **For stable structural context** (which venues exist, what metrics are tracked, rough market-share framing) — this doc, or `WebSearch` for recent third-party reporting.
2. **For live numbers from a specific panel** — open that panel on Dune, click through to the underlying query (the URL changes to `dune.com/queries/<id>`), grab the query ID, and hand it to Claude. Claude runs `python3 getDune.py <query_id>` against the Dune API. See CLAUDE.md for usage details.
3. **For bulk data** — Dune supports CSV export on any query page. Avery can download and drop the file in the repo, which is simpler than scripting the API for one-off pulls.

**Important:** one Dune "dashboard" is made up of many independent queries — one per chart. There's no "fetch the whole dashboard" API. If Avery gives Claude only a dashboard URL, Claude will ask him to click into the specific panel he actually wants.

## Staleness warning

Numbers on this dashboard update weekly. Anything we quote from memory or from this doc is a point-in-time snapshot and will drift. When a number actually matters — sizing a venue, ranking contracts by volume, arguing about market share — pull fresh data rather than trusting cached framing.
