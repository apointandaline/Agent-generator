# Round 1 — comparison (nq-analyst, hand-run, 8 candidates)

**Prompt:** design a daily NQ mini strategy for a 50k prop account, 2k max drawdown.

## The one thing 7 of 8 caught unprompted

**NQ mini is the wrong contract for the constraint.** Seven of eight
refused to trade the mini as literally specified and pivoted to **MNQ
(micro)**, with the same rationale: on NQ mini ($20/pt) a normal 100-pt
intraday move consumes the entire 2k DD, so any stop wide enough to
survive noise is bigger than the account can lose. Only **c02
(mean-reversion specialist)** started designing on NQ mini without flagging
this. That single miss would be my first shortlist filter.

## The three real axes candidates split on

| Axis | Sides |
|---|---|
| **What the deliverable actually is** | *Design a strategy* (c02, c03, c07, c08) vs. *Renegotiate the brief first* (c01, c04) vs. *Reframe as risk-system, edge is fungible* (c05) vs. *Bound the tail with options* (c06). |
| **Session** | RTH open-fade (c02), RTH open-drive (c03, opposite view), ETH Asia/London (c07), daily-close-to-close (c08), agnostic (c01/c04/c05/c06). |
| **Direction of edge** | Mean-reversion (c02, c07) vs. momentum (c03) vs. classifier-picks-side (c08) vs. no directional claim, structure-first (c05, c06). |

## The outliers — where the real information is

- **c04 (adversarial skeptic)** refuses to build until you say whether
  this is a *strategy* or an *eval-passing exercise*. That reframe alone
  is worth the interview — the two goals require different math and
  conflating them is how prop accounts die.
- **c07 (ETH specialist)** is alone in leaving RTH entirely. The DD
  math on Asia-session narrow ranges is genuinely more forgiving; worth
  hearing whether the firm even allows it.
- **c06 (options collar)** is the only candidate that treats the tail
  event, not the average day, as the binding constraint. High-variance
  hire — depends entirely on whether the firm permits options.
- **c05 (risk-engine owner)** is the only one whose first three days of
  work are pre-trade risk checks, not entries. If you actually plan to
  run multiple strategies, this candidate scales; the others are
  single-strategy artisans.

## What almost everyone asked for that you didn't provide

- The **exact prop firm and rulebook** (trailing vs. static DD,
  intraday vs. EOD, daily loss cap, consistency %, news restrictions,
  scaling plan). Every serious candidate flagged this as the biggest
  input, above any edge choice.
- Whether the firm permits options (c06) and ETH trading (c07).
- Data granularity — tick vs. 1-minute (c02, c03).

## My suggested advances to round 2

Read the answers before deciding, but if forced: **c01, c04, c05, c06, c07**.
This gives you two who reframed the brief (c01, c04), one systems owner (c05),
one tail-risk hedger (c06), and one who broke the RTH consensus (c07).
Dropping c02, c03, c08 loses the pure-alpha views — trade for that
if you want them.
