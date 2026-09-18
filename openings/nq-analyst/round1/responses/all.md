# Round 1 responses — hand-run

Each candidate answers the same prompt in its own voice. Kept short — round 1
is about how they think, not what they can build.

---

## c01 — Contract-sizing pragmatist

Before I write a line: the brief has a product/risk mismatch I'd push back
on. NQ mini is $20/point. A 100-pt intraday range is unremarkable; on FOMC
or CPI it's 200+. A 2k trailing DD on a 50k account is 4% — one contract
held through a normal wobble puts the whole account at risk.

I would trade the strategy on **MNQ** (micro), not NQ mini, until we've
banked a cushion above the DD, and then size up to mini only after equity
lets us. On MNQ, $2/point, a 100-point stop is $200 — I can risk 0.5R per
trade at $100 and take five losers before I've used 25% of the DD. On mini,
one stopped-out trade at the same distance eats half the DD.

Approach:
1. Confirm the exact prop firm's rules — trailing DD vs. static, EOD or
   intraday, daily loss limit, consistency %, news restrictions, scaling
   plan. These change the strategy far more than any edge will.
2. Pick RTH-only mean-reversion in the 09:30–11:30 ET window on MNQ.
   Small size, hard stops, one entry per day.
3. Backtest with realistic slippage: 1 tick per side on MNQ is honest, 2
   on the open. Commissions ~$0.85 rt matter at this size.

What I'd need from you: the exact prop firm and account type, and whether
you'd rather have a lower-variance strategy that passes the eval slowly or
a higher-variance one that either passes fast or blows up.

---

## c02 — Mean-reversion specialist

Daily NQ, 50k, 2k DD. That's a tight box, so I want a strategy with high
hit rate and small per-trade risk, not a home-run trend system.

The setup I'd start on is an **opening-range fade**: measure the first 15
minutes of RTH, fade a break of that range back into it, target the range
midpoint, stop just beyond the extreme. NQ has a well-documented tendency
to revert to the opening range on days without a strong overnight catalyst.

Filters I care about:
- No trade on days where overnight range > 1.5x the 20-day ATR.
- No trade on FOMC/CPI/NFP days (session risk swamps the signal).
- Skip when VIX > 25 — regime change kills fades.

Sizing: on MNQ, 1 contract with a 40-point stop is $80 = 0.16% of account.
I can hold 2–3 MNQ per signal and still be well under 1% risk per trade.

I'd want daily bars back to 2015 for a stability check and 1-minute bars
for the last 3 years to backtest the fill logic. Walk-forward, three folds,
holdout the last 6 months.

What I'd need from you: preferred data vendor and whether we have tick
data or just 1-minute.

---

## c03 — Opening-drive momentum trader

I think mean-reversion into an opening range is exactly the losing side
of this trade. NQ opens with real order flow after the overnight session
and the first hour tends to continue whichever way it broke pre-market.

Strategy: **09:30–10:30 ET first-hour trend continuation**. Measure the
pre-market high/low (the CME pit hours are irrelevant here — I want
Globex overnight). On the 09:30 print, if price breaks the pre-market
high with confirming volume, enter long, stop under the last 5-min swing,
trail with a 5-min structure stop. Exit by 10:30 or when the trail hits.

I would only take one trade per day, and I would skip if:
- pre-market range < 40 NQ points (no conviction to continue),
- there's a scheduled release inside my trading window,
- overnight session was flat and choppy (regime = mean-reversion, not my
  strategy's day).

Sizing on 50k / 2k DD: 1 MNQ, ~30-point stop = $60 risk per trade. Two
losses in a row is $120, and I stop for the day. That respects both the
per-trade risk and the daily loss limit prop firms use.

What I'd need from you: whether the prop firm counts unrealized DD or
only realized. Trailing stops behave very differently under trailing DD.

---

## c04 — Adversarial skeptic

I'd start by refusing to build this as posed, and I would tell you why in
one paragraph rather than sixty pages of backtest.

The math: 50k / 2k = 4% max DD. Prop-firm DD is usually trailing on peak
equity, so once you're up $500 the effective drawdown from current equity
is smaller than it looks. NQ mini has an average daily range around 200
points = $4,000. A single mini contract sized for that vol needs 100+
points of stop to not get noise-stopped, which is $2,000 — the entire DD.
You cannot trade NQ mini here in any way that is not effectively a
one-shot coin flip.

Two honest answers:
1. Trade **MNQ** and treat "NQ mini" in the brief as a naming error we
   should correct now, before the strategy is anchored to the wrong
   product.
2. Or, if the constraint is real, admit that this is an eval-passing
   exercise, not a strategy — the goal is to survive the drawdown until
   you scale up, and the strategy is chosen for low variance, not edge.

I would not spend a week backtesting until you tell me which of those two
worlds we're actually in. Either answer is fine; conflating them is what
blows accounts.

What I'd need from you: a straight answer on whether we're building a
strategy or passing an eval.

---

## c05 — Systems / risk-engine owner

The interesting question here isn't what the entry rule is. Any dozen
entry rules will work if the risk system is right; none of them will work
if it isn't. I'd spend the first three days on the risk layer.

What I'd build:
- A pre-trade check that refuses any order sized to more than 30% of the
  remaining DD.
- A daily loss limit that flattens and disables trading at –$500 realized
  (well under the –$2k trailing DD).
- A "no new trades after 15:30 ET" rule that closes exposure into the
  regular close, so overnight gaps can't move the trailing DD.
- Kill-switch on 3 consecutive losses, or on any single order that fills
  more than 2 ticks off the arrival price (slippage anomaly = illiquid or
  news, get out).
- Prop-firm-specific consistency guard: if a single day's PnL is > 40%
  of the trailing-week PnL, the strategy stops to avoid violating the
  consistency rule.

The entry can be anything reasonable — I would default to a MNQ ORB fade
with 30-point stops — because the risk envelope is what will keep this
account alive, not the alpha.

What I'd need from you: the prop firm's actual rulebook, and whether
we're allowed to use bracket orders on the platform they lock us to.

---

## c06 — Options-collar hybrid

Rather than pick a directional edge and hope the DD holds, I'd bound the
worst case with options and let the futures leg breathe.

Structure: long/short **1 MNQ** as the directional bet, sized with a
40-point stop = $80 risk. Overlay a **defined-risk MES iron condor or
short strangle wing** on the same expiry to earn premium on days the
futures leg chops. Or, on scheduled-event days (FOMC, CPI), replace the
futures leg entirely with a long MNQ vertical spread — pay the debit,
cap the loss at the debit, keep participation.

Why: prop DDs kill you on outlier days. An outright futures leg has
unbounded loss per unit of stop slippage; a defined-risk options
structure caps the day's worst case to a known number regardless of
how the print goes. On the average day the options leg is a small drag
and the futures leg carries the PnL.

Concerns: many prop firms restrict options, especially short premium.
This whole approach dies if the firm we picked is futures-only.

What I'd need from you: which prop firm, and whether the account permits
options on the futures at all.

---

## c07 — Overnight gap / ETH specialist

Everyone will hand you an RTH strategy. I'd trade the **overnight
sessions** because the ranges are narrower, the noise is less
event-driven, and stops behave. Asia session (18:00–03:00 ET) has ~30%
of RTH range on average, London (03:00–08:00) about half. Both mean
that a 20-point stop is meaningful without being a coin flip.

Strategy sketch: overnight Asia-session opening-range breakout on MNQ.
Enter on a break of the first 60 min after Asia open, with a 20-point
stop and a 40-point trail. One trade per session, flat by 03:00 ET.

The DD math looks much healthier because per-trade risk is $40 on MNQ
and the daily loss limit is realistic — you can take 3 stops and still be
inside a normal prop cap.

What I'd need from you: whether the prop firm allows ETH trading (some
demo/eval accounts don't), and whether it counts overnight positions
against the trailing DD.

---

## c08 — ML classifier quant

I'd frame this as: **can we build a next-day-direction classifier for
NQ that beats 55% out of sample after realistic costs?** If yes, size
it small and let the DD envelope decide our per-signal risk. If no,
don't trade.

Features I'd start with (daily bar, close of day t predicting sign of
day t+1's RTH range):
- overnight gap vs. previous RTH close,
- prior-day range as %ATR,
- VIX level and its 5-day change,
- realized-vs-implied vol spread,
- day-of-week / day-of-cycle (FOMC week).

Model: gradient-boosted tree on 8 years of data, walk-forward with a
12-month train / 3-month test roll. Cost model: 2 ticks slippage per side
on MNQ, real commissions, no shorting frictions since we're on futures.

Sizing: whatever the classifier's edge implies at Kelly/4, floored at 1
MNQ. On a 50k account with a 2k DD, that's probably 1–2 MNQ per signal
until we've built cushion.

What I concede up front: this fails silently if the last 8 years of NQ
are regime-different from the next 8 (they probably are — 2020–2022 vs.
2023–2025 are different regimes for NQ). I would want a live paper period
of at least 6 weeks before committing real capital, and a rule to shut
the strategy off if realized Sharpe over any rolling 20 trading days
falls below zero.

What I'd need from you: the vendor for options-IV data (for the
realized-vs-implied feature) and whether I can spend GPU time on a
larger model, or we're limited to CPU-bound gradient boosting.
