---
title: NQ volatility profile — daily range, and what it implies for small accounts
role: index-futures
tags:
- nq
- volatility
- atr
- risk
- position-sizing
source: Market volatility statistics and futures research, retrieved 2026-09
origin: manual
added_at: '2026-09-18T02:20:00Z'
sources:
- title: "NQ Futures Volatility: Stops, Sizing, and Strategy"
  url: https://volatilitybox.com/research/nq-futures-volatility/
- title: "ES and NQ Futures Average Daily Range: Volatility Statistics"
  url: https://youngmoneyinvestments.com/blog/es-nq-futures-average-daily-range-statistics
- title: "NQ futures trading: a data-driven guide"
  url: https://www.edgeful.com/blog/posts/nq-futures-trading
---

# How much NQ actually moves

Recent figures (2025-26, verify before relying on them — volatility regimes shift):

- NQ New York session 14-day ATR has run roughly **410-450 points**, with the
  range exceeding ATR on something like a third of days.
- A normal day moves **200-450 points**, i.e. **$4,000-$9,000 per NQ contract**.
- NQ runs roughly **1.3-1.5×** the percentage range of ES, driven by tech
  concentration — the largest handful of names are about half the index.
- Peak earnings weeks (late Jan, Apr, Jul, Oct) expand daily ATR by a further
  **20-35%**.

## The implication that catches people out

Take a $50,000 prop account with a $2,000 trailing drawdown, trading one NQ
contract at $20 per point:

```
$2,000 / $20 per point = 100 NQ points of total room
```

100 points is roughly **a quarter of a single day's ATR**. That is the entire
account's lifetime loss allowance, not a per-trade stop. A daily-timeframe
strategy on one NQ contract therefore has a stop distance far inside the
instrument's ordinary noise — it will be stopped out by random intraday movement
long before any edge can express itself, and the trailing floor means there is no
recovery path afterwards.

The same account on **MNQ at $2 per point** has 1,000 points of room, roughly
**2.4× daily ATR**. That is a tradeable budget, and it is why the micro exists.

## What a competent analyst does with this

Range-to-budget arithmetic comes *before* signal research, not after. The correct
response to a mandate whose implied stop is a fraction of daily ATR is to say the
mandate is arithmetically unsound and to present the options:

1. Trade MNQ instead of NQ (10× finer risk granularity).
2. Shorten the holding period so the relevant volatility measure is intraday
   range rather than daily ATR — but note this contradicts a "daily timeframe"
   mandate and changes the strategy being asked for.
3. Increase the drawdown budget or reduce the required position size.
4. Accept a very low expected pass rate and size the attempt as a lottery ticket,
   stated honestly as such.

Proposing a daily NQ strategy inside a $2,000 trailing drawdown without
confronting this arithmetic is the single clearest signal that an analyst does
not know the product.
