# Agent Generator

An interview process that produces specialised AI agents.

You are the CEO. You post an opening, and the tool sources 50 candidate agents by
researching the role on the web. You interview them across three rounds — a
discussion round, a hands-on technical round, and an executive review — and at
each stage **you** decide who advances. The winner is written out as a reusable
agent system prompt with its own skills directory.

The point is not to automate the hire. It is to make you look at 50 different
answers to "what should this agent actually be" before you commit to one.

```
                  50 candidates          25 candidates        5 finalists
 opening ──► research ──► ROUND 1 ──► you pick 5 ──► ROUND 2 ──► you pick 5 ──► ROUND 3 ──► you hire 1
             (web + KB)   discussion    ▲             build for real   ▲       exec brief        │
                                        └── winners seed the next round┘                        ▼
                                                                                     agents/<name>.md
```

## Install

```bash
pip install -e .          # or: pip install -r requirements.txt
hire init
hire backends             # what can drive the model here
```

## Backends — you do not need an API key

The funnel can be driven three ways. `hire backends` shows which are usable, and
`--backend` (or `$HIRE_BACKEND`) picks one; the default is `auto`.

| Backend | Needs | Notes |
|---|---|---|
| `api` | `ANTHROPIC_API_KEY`, or an `ant auth login` profile | Cheapest per call. Structured outputs are schema-enforced. |
| `claude-cli` | the `claude` CLI, signed in | Uses the Claude Code CLI's own credentials — no key of your own. |
| `codex-cli` | the `codex` CLI, signed in | Same idea for OpenAI's CLI. |

`auto` prefers `api` when credentials exist and falls back to whichever CLI is
installed, so a machine with `claude` already signed in can run the whole funnel
with no further setup.

**The CLI backends cost more per call.** Each one spawns a fresh agent session
that re-sends its own system prompt — roughly 20k cache-write tokens per call
even with `--restricted`, so a trivial call has a floor of several cents. They
are the right choice when you have a CLI subscription and no API key, or when you
want round 2 run by a real coding agent.

**Round 2 differs by backend.** On `api`, candidates get the confined
`write_file` / `read_file` / `list_files` tools in this repo and `--allow-exec`
adds a sandboxed `run_command`. On the CLI backends, the agent works natively in
the workspace directory with its own file tools, which generally produces better
builds. One caveat: Codex needs `workspace-write` to create files at all, and
that sandbox also permits commands inside the workspace — so a Codex round 2 is
never strictly no-execution. Use `api` or `claude-cli` if that distinction
matters to you.

**Structured output is enforced only on `api`.** The CLI backends are asked for
JSON and their replies are recovered from prose or code fences. That is reliable
in practice but not guaranteed, so a screening call can occasionally fail and be
reported as a skipped candidate.

## The workflow

### 1. Post the opening

```bash
hire open --id quant-qa \
  --role "QA engineer for trading strategies" \
  --brief-file examples/opening-brief-quant-qa.md \
  --qualifications "Python, pandas, backtest methodology, execution realism"
```

Your brief is the single most important input. Be specific about what the agent
is *for* and what a bad outcome looks like. `examples/opening-brief-quant-qa.md`
shows the level of detail that works.

### 2. Research the role

```bash
hire research quant-qa
```

Searches the web for how the role is actually practised — current tooling, real
failure modes, what separates strong practitioners from adequate ones — and
writes a briefing to `openings/quant-qa/research/findings.md`. The same briefing
is filed in your local knowledge base so later openings can reuse it.

### 3. Generate the slate

```bash
hire generate quant-qa --round 1
```

First designs 50 *distinct archetypes* — deliberately spread across philosophies,
backgrounds and risk appetites, including profiles a conventional process would
screen out — then writes each one as a full agent. Each candidate lands in
`openings/quant-qa/round1/candidates/cNN.md`. The body of that file is a working
system prompt; the frontmatter is its résumé.

### 4. Round 1 — the discussion

Write a mock project prompt (see `examples/round1-mock-project.md`), then:

```bash
hire round1 quant-qa --prompt-file examples/round1-mock-project.md
```

Every candidate answers *as itself*, using its own system prompt, describing how
it would approach your project. Answers land in `round1/responses/`.

A screening pass then scores all 50 against a rubric and writes
`round1/leaderboard.md`. **Those scores are advisory** — they exist so you can
triage 50 answers, not so the model can pick for you. Read the top candidates'
actual answers, then record your decision:

```bash
hire shortlist quant-qa --round 1 \
  --advance c07,c19,c23,c31,c44 \
  --notes-file examples/ceo-notes-round1.json
```

Your notes matter: they are fed directly into the next round's generation.

### 5. Round 2 — the technical project

```bash
hire generate quant-qa --round 2
```

Each of your 5 winners is regenerated into 5 sharper descendants (25 total),
seeded on the winner's own markdown, its round-1 answer and your notes. Each
descendant is pushed along a different axis — deeper specialist, systems owner,
adversarial skeptic, tooling builder, business translator.

```bash
hire round2 quant-qa --project-file examples/round2-technical-project.md
```

This round is real work. Each candidate gets its own workspace and file tools,
and actually builds the thing — code, tests, a report format, a `SUBMISSION.md`.
Compare the artifacts in `round2/workspaces/<candidate>/`, then:

```bash
hire shortlist quant-qa --round 2 --advance c07-v2,c19-v1,c23-v4,c31-v3,c44-v1
```

#### About `--allow-exec`

By default candidates can only read and write files — they cannot run anything.
Pass `--allow-exec` and they can run shell commands to test their work, which
produces noticeably better submissions.

That flag executes model-written code on your machine. Commands run from the
candidate's own workspace with your Anthropic credentials stripped from the
environment, but **this is not a sandbox** — a command can still reach the
network and the rest of the filesystem. Use it inside a container or a throwaway
VM. The CLI asks for confirmation before enabling it.

### 6. Round 3 — the executive review

```bash
hire round3 quant-qa
```

Produces `round3/exec-brief.md`: a decision table, a skills matrix, what each
round revealed about each finalist (including any who interview better than they
build), where the finalists genuinely differ, and what each choice commits you
to. It ends with a clearly-marked advisory read. You decide.

```bash
hire hire quant-qa --candidate c19-v1 --name backtest-qa \
  --notes "Always lead with the bias findings. Never soften a negative verdict."
```

### 7. Use the agent

```
agents/backtest-qa.md            the agent's system prompt (Claude Code subagent format)
agents/backtest-qa/dossier.md    how it was hired, and what to supervise
agents/backtest-qa/skills/       drop skills here as you use it
```

Copy `agents/backtest-qa.md` into `.claude/agents/` to use it as a Claude Code
subagent, or paste its body as a system prompt anywhere else.

## The knowledge base

`kb/entries/` holds everything the system knows about your roles. Entries are
plain markdown with YAML frontmatter, tagged by `role` — that tag is how
candidate generation finds them.

```bash
hire kb list                                  # everything
hire kb list --role quant-qa                  # what a given agent draws on
hire kb show quant-qa-role-research
hire kb search "look-ahead bias"
hire kb add --file notes.md --role quant-qa --title "Our execution assumptions" \
            --tags execution,costs
```

Adding a file to `kb/entries/` by hand works exactly as well as the CLI — give it
frontmatter with `title`, `role` and `tags` and it is picked up on the next run.
This is how you inject house knowledge that no amount of web research would
find: your data vendors, your risk limits, the things that burned you before.

## Keeping an eye on cost

Every API call is logged to `openings/<id>/ledger.jsonl`.

```bash
hire status quant-qa                          # where you are, what to run next
hire cost quant-qa                            # spend so far, by stage
hire generate quant-qa --round 1 --dry-run    # estimate before committing
```

`--dry-run` works on every expensive command. Round 1 at 50 candidates is the
largest single step; round 2 is the most variable, because a candidate that uses
its full turn budget building software costs many times one that finishes early.

## Tuning

`openings/<id>/opening.yaml` is yours to edit between steps:

- `funnel:` — the 50 / 5 / 25 / 5 / 1 counts
- `models:` — per-stage model, effort and token budget
- `rubric:` — what the advisory screening scores, and the weights
- `variant_axes:` — the five directions round-2 descendants are pushed in

Everything defaults to `claude-opus-5`. If you want to spend less on the wide
round-1 pass, set `models.candidate.model` and `models.round1.model` to
`claude-sonnet-5` and leave round 2 and the brief on Opus.

## Command reference

| Command | What it does |
|---|---|
| `hire init` | Create the directory layout |
| `hire open` | Define an opening |
| `hire research <id>` | Web-research the role, file it in the KB |
| `hire generate <id> --round {1,2}` | Generate candidates |
| `hire round1 <id> --prompt-file F` | Run the discussion round |
| `hire round2 <id> --project-file F` | Run the build round |
| `hire screen <id> --round N` | Re-run advisory scoring |
| `hire shortlist <id> --round N --advance IDS` | **Record your decision** |
| `hire round3 <id>` | Build the executive brief |
| `hire hire <id> --candidate ID --name N` | Hire, and emit the agent |
| `hire status [<id>]` | Where things stand, and the next command |
| `hire cost <id>` | Estimated spend by stage |
| `hire show <id> --candidate ID [--response]` | Print a candidate or an answer |
| `hire kb ...` | Inspect and extend the knowledge base |
| `hire backends` | Show which model backends are usable here |

## Tests

```bash
python -m pytest tests/ -q
```

The suite drives the entire funnel against a fake backend, so it runs offline and
costs nothing. The CLI backends' argv construction and output parsing are tested
against a recorded subprocess; the live paths are exercised by running the tool.
