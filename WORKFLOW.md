# How the process is designed

Notes on why the workflow is shaped this way, and how to run it well.

## The CEO decides; the model never does

Nothing advances a round except a recorded decision from you. `hire shortlist`
and `hire hire` are the only commands that move the funnel forward, and neither
consults a model.

The screening pass exists because reading 50 interview answers carefully is not
a realistic ask. It scores each submission independently against a rubric and
gives you a leaderboard, a one-line read per candidate, and — most usefully — a
probe question aimed at each candidate's weak point. Treat it as a research
assistant's triage, not a verdict. The assessor is told explicitly that it does
not make hiring decisions, and the leaderboard repeats this at the top.

Two habits make the advisory scores more useful:

- **Read outside the top 5.** The scorer rewards rigor and specificity. A
  candidate with an unusual angle and a mediocre score is often the one worth
  advancing, and it is exactly what a ranked list buries.
- **Write real notes when you shortlist.** They are not a record — they are fed
  into round-2 generation as instructions. "Keep the skepticism, lose the
  hedging language" measurably changes what the next 5 descendants look like.

## Why the rounds are shaped this way

**Round 1 tests judgement, not output.** Candidates talk through a mock project.
The questions asked — what you're actually asking for, where this goes wrong,
what I'd need from you — are designed so that a vague candidate cannot hide. The
most informative section is usually "what I'd need from you": a candidate that
asks nothing is one that will guess later.

**Round 2 tests execution, and it has to be real.** Each candidate gets its own
workspace and file tools and actually builds. This is the round that separates
agents that interview well from agents that work, and it is the reason the
descendants are worth generating: you are comparing artifacts, not prose.

**Round 3 is comparison, not summary.** The brief is told to flag finalists that
are substantially the same agent, and to surface any candidate whose round-1
answer and round-2 build disagree. That disagreement is the single most useful
signal in the whole process.

## Why winners are regenerated rather than carried forward

The round-1 winner is not the agent you want — it is evidence about what you
want. Five candidates each spawn five descendants seeded on the parent's system
prompt, the parent's round-1 answer and your notes, each pushed along a different
axis:

| axis | what it changes |
|---|---|
| `deeper_specialist` | narrows scope, goes deeper on the hardest technical core |
| `systems_owner` | widens ownership to the surrounding pipeline and its health |
| `adversarial_skeptic` | leads with falsification — hunts for silent wrongness |
| `tooling_builder` | builds durable internal tooling over one-off analyses |
| `business_translator` | anchors everything to P&L, risk limits and decisions |

Edit `variant_axes` in `opening.yaml` to change them. They are the main lever on
what round 2 explores, and they are worth tailoring per role — the axes that
matter for a PM agent are not the ones that matter for QA.

## Choosing what to give candidates

**The mock project (round 1)** should be a real situation with a trap in it. The
example — a suspiciously good backtest whose author mentions in passing that they
tried several lookback windows — works because the tell is stated plainly but
never flagged. Candidates that notice it are showing you something.

**The technical project (round 2)** should be scoped to something genuinely
finishable. Candidates work under a turn budget; an over-scoped project produces
25 half-finished submissions that are hard to tell apart. Ask for fewer things,
done properly, and say so in the brief.

## The knowledge base is the long-term asset

Openings come and go; `kb/entries/` accumulates. Two kinds of entry live there:

- **Researched** — written by `hire research`, tagged with the opening's role.
- **Yours** — house knowledge the web does not have. Your data vendors and their
  quirks, your risk limits, your execution assumptions, the postmortem from the
  strategy that blew up. Add these with `hire kb add` or by dropping a markdown
  file with frontmatter into `kb/entries/`.

Anything tagged with a role, plus anything tagged `general`, is fed into
candidate generation for openings with that `kb_role`. Set `kb_role` in
`opening.yaml` to point several openings at one shared body of knowledge — for
example, every trading-side role drawing on `kb_role: trading`.

The knowledge base is the reason the second agent you hire is better than the
first.

## Cost, realistically

Round 1 is 50 candidate generations plus 50 interviews plus 50 screenings, all
on the default model. Round 2 is 25 agent sessions that each run a multi-turn
build loop, which is the most variable cost in the system by a wide margin.

Use `--dry-run` before every expensive step. If you want to spend less, the right
place to economise is the wide round-1 pass — set `models.candidate.model` and
`models.round1.model` to `claude-sonnet-5` and keep round 2, the brief and the
final refinement on Opus, where the decisions actually get made. `hire cost`
reports what you have spent by stage.

## Running the same opening twice

Nothing is destructive except regenerating a round's candidates, which overwrites
that round's candidate files. Interview answers, workspaces, decisions and the
ledger all accumulate. If you want to explore a different direction from the same
round-1 slate, record a different shortlist and re-run `hire generate --round 2`
— the previous round-2 candidates are replaced, but round 1 is untouched.

To re-interview a subset rather than the whole slate, use `--only`:

```bash
hire round1 quant-qa --prompt-file second-prompt.md --only c07,c19,c23
```

## Limits worth knowing

- **`--allow-exec` is not a sandbox.** Commands are confined to the candidate's
  workspace directory and run without your Anthropic credentials, but they can
  reach the network and the wider filesystem. Run that mode in a container.
- **Web research depends on what search returns.** If a run surfaces no sources,
  `findings.md` says so at the top — treat that briefing as weaker evidence.
- **A failed candidate is skipped, not retried.** Generation and interview
  rounds report failures and carry on, so one API error cannot sink a 50-way
  round. Re-run the same command with `--only <ids>` to fill the gaps.
- **Advisory scores are not comparable across openings.** The scorer calibrates
  within a slate, not across them.
