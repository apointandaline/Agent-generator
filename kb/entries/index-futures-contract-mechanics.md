---
title: CME equity index contract mechanics — ES, MES, NQ, MNQ
role: index-futures
tags:
- contract-specs
- nq
- es
- micros
- position-sizing
source: CME Group contract specifications and broker references, retrieved 2026-09
origin: manual
added_at: '2026-09-18T02:20:00Z'
sources:
- title: E-mini Nasdaq-100 Futures Contract Specs — CME Group
  url: https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html
- title: Micro E-mini Nasdaq-100 Index — CME Group
  url: https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.html
- title: "ES vs MES vs NQ vs MNQ: What's the Difference"
  url: https://propfirmgorilla.com/resources/es-vs-mes-vs-nq-vs-mnq-differences
---

# Contract mechanics for the CME equity index complex

These are the numbers every strategy for this desk is constrained by. An analyst
who does not have them cold will propose strategies that cannot be traded.

## The four contracts

| Contract | Underlying | Multiplier | Tick | Tick value | 1 point |
|---|---|---|---|---|---|
| ES (E-mini S&P 500) | S&P 500 | $50 × index | 0.25 | $12.50 | $50 |
| MES (Micro E-mini S&P) | S&P 500 | $5 × index | 0.25 | $1.25 | $5 |
| NQ (E-mini Nasdaq-100) | Nasdaq-100 | $20 × index | 0.25 | $5.00 | $20 |
| MNQ (Micro E-mini Nasdaq) | Nasdaq-100 | $2 × index | 0.25 | $0.50 | $2 |

The micros are exactly 1/10th the notional of their minis, with the same tick
increment. That 10:1 ratio is the single most important sizing lever available
on a small account: it is the difference between a granularity of $20 per point
and $2 per point.

## Why this matters for sizing

Risk per trade is `points_at_risk × point_value × contracts`. On a constrained
account the point value is not a detail — it sets the minimum resolution of risk
you can express. A strategy whose stop distance is dictated by market structure
cannot be sized below one contract, so if one contract's stop already exceeds the
risk budget, the strategy is untradeable on that product regardless of how good
the signal is.

Always compute the *implied stop distance in points* that the risk budget allows,
then compare it to the instrument's actual volatility, before evaluating any
signal. If the implied stop is a small fraction of a typical session's range, the
strategy will be stopped out by noise and the edge never gets a chance to express.

## Session and roll

NQ and ES trade on CME Globex nearly 24 hours, Sunday 18:00 ET through Friday
17:00 ET, with a daily maintenance break. Contracts are quarterly (Mar/Jun/Sep/Dec)
and cash-settled. Any multi-year backtest must handle the quarterly roll — a
continuous series stitched without adjusting for the roll gap will manufacture
phantom gaps and phantom PnL. State which adjustment method was used
(back-adjusted, ratio-adjusted, or unadjusted with per-contract series), because
the choice materially changes backtested returns.

## Margin

Exchange initial margin for NQ has recently sat in the low-to-mid five figures
per contract and changes with volatility; brokers offer much lower intraday day-
trade margins. Margin is not the binding constraint on a prop evaluation account
— the drawdown rule is. Do not confuse the two: a position can be well within
margin and still be far too large for the drawdown budget.
