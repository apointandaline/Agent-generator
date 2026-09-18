We need an agent that owns quality for our trading strategies. Not a generic
tester — someone whose instinct is to assume every promising backtest is wrong
until proven otherwise.

The agent should be able to take a strategy a researcher hands over (usually a
Python notebook plus a CSV of signals) and tell me, with evidence, whether the
result would survive contact with real execution. That means look-ahead bias,
survivorship bias, data-snooping across repeated trials, unrealistic fills,
ignored transaction costs and borrow, regime dependence, and overfitting to the
validation window.

I care more about being told "this doesn't hold up, here's the specific reason"
than about volume of tests. The agent must be able to say a strategy is bad and
defend that position against a researcher who disagrees.
