# Technical project: build the bias-detection harness

Build the first version of the tooling you would actually use to vet a strategy
backtest before it goes anywhere near capital.

You have a workspace. Produce real files.

## What it must do

Given a backtest result — assume a tabular file with one row per trade
(`timestamp`, `symbol`, `side`, `entry_price`, `exit_price`, `qty`) and a
separate daily price series — your harness should detect and report on at least:

1. **Look-ahead bias** — any trade whose entry decision could only have been made
   with information not available at entry time.
2. **Survivorship bias** — symbols present in the trade log but absent from the
   price series for part of the period, or a universe that only contains names
   that still exist.
3. **Cost sensitivity** — how the reported PnL degrades as you apply realistic
   spread, slippage and commission assumptions. Show where the strategy breaks
   even.
4. **Overfitting signal** — at least one concrete, defensible measure that flags
   a result likely to have come from repeated trials.

## What I want to see

- Working code, organised the way you would actually organise it.
- Tests that demonstrate each detector firing on a case you construct.
- A short report format showing what the CEO sees when they run this.
- `SUBMISSION.md` explaining what you built, what you decided, how I verify it,
  and what you would not trust yet.

Synthesise your own small sample data. Scope this to something you can genuinely
finish — I would rather see three detectors that work than six that are stubs.
