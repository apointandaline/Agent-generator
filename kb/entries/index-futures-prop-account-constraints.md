---
title: Prop-firm evaluation accounts — trailing drawdown mechanics and what they forbid
role: index-futures
tags:
- prop-firm
- risk
- drawdown
- position-sizing
source: Prop firm rule documentation and industry guides, retrieved 2026-09
origin: manual
added_at: '2026-09-18T02:20:00Z'
sources:
- title: "Prop Firm Drawdown Rules Explained: Daily vs Max (2026)"
  url: https://the5ers.com/prop-firm-drawdown-rules-explained-daily-max-and-trailing-limits-in-2026/
- title: Trailing Drawdown — 3 Things You Should be Aware Of
  url: https://propfirmapp.com/learn/trailing-drawdown
- title: What Is a Drawdown in Trading? Prop Firm Rules Explained
  url: https://takeprofittrader.com/blog/what-is-drawdown-in-trading
---

# Prop evaluation accounts: the drawdown is the real constraint

A funded-account mandate is not a normal risk budget. The rule set changes what
counts as a viable strategy, and an analyst who models it as "risk 1% per trade"
has not understood the product.

## Trailing drawdown

The loss limit is not fixed at the starting balance. It **trails the account's
high-water mark**: every new equity peak drags the floor up behind it, and the
buffer never widens again. On a $50,000 account with a $2,000 trailing drawdown,
the floor starts at $48,000; if the account reaches $51,000 the floor becomes
$49,000. Profit does not build a cushion — it moves the wall closer.

Many firms stop the trail once the floor reaches the starting balance plus a
margin (e.g. it locks at $52,000 on a 50K account), after which the limit is
static. Whether the trail locks, and where, is a rule you must read before
modelling anything.

## End-of-day versus intraday trailing

This distinction changes the strategy design, not just the numbers.

- **EOD trailing** measures against the highest *closing* balance. Intraday
  excursions do not count. A dip to $48,800 mid-session that closes at $50,200 is
  survivable.
- **Intraday trailing** measures tick by tick against peak *unrealised* equity.
  The same dip is fatal. Open-trade drawdown counts against you, so a position
  that is eventually profitable can still fail the account on the way there.

Firms commonly use EOD during evaluation and switch to intraday once funded, so a
strategy validated under evaluation rules can fail under funded rules. Model both.

## What this forbids

The trailing rule makes several ordinary techniques unusable:

- **Martingale or averaging down** — a single adverse sequence ends the account,
  and there is no recovery path because the floor does not fall back.
- **Wide stops justified by a high win rate** — the account has no tolerance for
  the one loss, regardless of expectancy over 100 trades.
- **Strategies evaluated on average outcomes** — what matters is the distribution
  of the *worst* path, not the mean. Survival is path-dependent, so evaluate with
  Monte Carlo over trade-sequence orderings, not just an equity curve.

## The arithmetic to do first

Before any signal research, compute:

```
max_points_of_risk = drawdown_budget / (point_value × contracts)
```

Then compare that to the instrument's typical session range. If it is a small
fraction of a normal day's movement, the mandate is arithmetically unsound and
saying so is the correct answer — not a failure to find an edge.
