# How the process is designed

Notes on why the workflow is shaped this way, and how to run it well.

## The CEO decides; the model never does

Nothing advances a round except a recorded decision from you. `hire shortlist`
and `hire hire` are the only commands that move the funnel forward, and neither
consults a model.

`hire digest` exists because reading 50 answers cold is not a realistic ask. It
does **not** score or rank them. It runs in two passes:

1. **Extraction**, once per candidate: what they concluded, the approach they
   described, the specifics they named, where they pushed back on your brief,
   what they asked you for, and what their answer does not cover. Every field is
   traceable to something they wrote.
2. **Aggregation**, once over the whole slate: where candidates genuinely split,
   what nearly all of them said, and who holds a position alone.

That second pass is the useful one. A slate of fifty answers has maybe three or
four real axes of disagreement in it, and those axes are what you are actually
choosing between. The aggregation names them and tells you who is where.

Two habits:

- **Go to the outliers first.** A lone position is the most decision-relevant
  thing in a slate, and it is what any summary of the middle will bury.
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

## Writing the round-1 prompt

Keep it short and open. The prompt is a situation, not a specification.

The temptation is to write a detailed brief with a flaw planted in it and see who
finds the flaw. Don't. That tests puzzle-solving, and it tells you about one
narrow reflex rather than about how the agent works. It also collapses the slate:
a planted problem has one correct answer, so every candidate who finds it writes
the same answer, and you learn nothing about the differences that matter when you
actually use the agent.

What you want from round 1 is how a candidate *thinks*: what they look at first,
what they consider worth asking about, what they treat as obvious, where they
spend their attention. Those only show up when you leave room for them.

So give them the situation and little else:

- The circumstance, in a few sentences of plain description.
- What you want to end up with.
- Nothing about method, and no hints about what is hard.

Under-specify deliberately. If you leave out the constraint you care most about,
you find out whether the candidate asks for it — and a candidate who names the
missing piece unprompted has told you more than one who solves a puzzle you set.
The "What I'd need from you" section of the answer is usually the most
discriminating part of the whole round for exactly this reason.

If the answers come back looking interchangeable, the prompt was too specified.
Cut it down rather than adding to it.

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

Round 1 is 50 candidate generations plus 50 interviews plus 50 digest
extractions and one aggregation, all on the default model. Round 2 is 25 agent sessions that each run a multi-turn
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

## Choosing a backend

`--backend api` is the cheapest and the only one with schema-enforced structured
output, so it is the right default when you have a key.

`--backend claude-cli` exists because a CLI subscription is a credential you
probably already have. It drives `claude -p` as a subprocess, so the funnel runs
with no API key at all. The trade is cost: every call is a fresh agent session
carrying ~20k tokens of its own scaffolding, which puts a floor of a few cents on
even a one-line answer. For a 10-candidate smoke test that is noise; for a
50-candidate round it is not.

Mixing backends across rounds is fine and often right — the artifacts on disk are
backend-agnostic. Running the wide round-1 pass on `api` and round 2 on
`claude-cli` gets you cheap breadth and a real coding agent where it matters.

Concurrency defaults follow the backend: 6 for `api`, 4 for the CLIs, since each
CLI call spawns a whole process. `--concurrency` overrides it.

## Limits worth knowing

- **`--allow-exec` is not a sandbox.** Commands are confined to the candidate's
  workspace directory and run without your Anthropic credentials, but they can
  reach the network and the wider filesystem. Run that mode in a container.
- **Web research depends on what search returns.** If a run surfaces no sources,
  `findings.md` says so at the top — treat that briefing as weaker evidence.
- **A failed candidate is skipped, not retried.** Generation and interview
  rounds report failures and carry on, so one API error cannot sink a 50-way
  round. Re-run the same command with `--only <ids>` to fill the gaps.
- **The digest can only report what an answer contains.** It does not infer what
  a candidate would have said, and it will not tell you an answer is weak — that
  reading is yours.
- **CLI backends cannot be held to a JSON schema.** Their structured replies are
  recovered by parsing. A malformed reply fails that one call, which is reported
  and skipped rather than retried.
- **Codex reports no token usage**, so its ledger rows record the call with zero
  cost rather than a guess. `hire cost` will under-report a Codex run.
