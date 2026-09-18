"""A stand-in for :class:`hire.llm.LLM` so the funnel can be tested offline."""

from __future__ import annotations

from hire.llm import Result

USAGE = {"input_tokens": 1000, "output_tokens": 500}

CANDIDATE_BODY = """\
## Positioning
You are a test candidate.

## Operating principles
- You state assumptions.

## Method
1. Read the brief.
2. Do the work.

## Domain toolkit
pandas, pytest.

## What you refuse and escalate
You escalate anything touching live capital.

## Reporting contract
You report in markdown.

## Known weakness
You over-index on tooling.
"""


class FakeLLM:
    """Records every call and returns deterministic content."""

    def __init__(self, on_usage=None, slate_size=10):
        self.on_usage = on_usage
        self.calls = []
        self.slate_size = slate_size
        self._slate_n = 0

    def _record(self, stage, label, model="fake-model"):
        self.calls.append((stage, label))
        if self.on_usage:
            self.on_usage(stage, model, USAGE, 0.01, label)

    def text(self, stage, cfg, *, system, prompt, label="", prefill_messages=None):
        self._record(stage, label)
        if stage == "candidate":
            return Result(text=CANDIDATE_BODY, usage=USAGE, model=cfg.model)
        if stage == "refine":
            return Result(text="## Role\nYou are the hired agent.\n", usage=USAGE, model=cfg.model)
        return Result(text=f"# {stage} output for {label}\n\nbody", usage=USAGE, model=cfg.model)

    def json(self, stage, cfg, *, system, prompt, schema, label=""):
        self._record(stage, label)
        if stage == "slate":
            # Honour the count the caller asked for, as the real model would.
            import re

            match = re.search(r"Design (\d+) distinct", prompt)
            want = int(match.group(1)) if match else self.slate_size
            data = {
                "archetypes": [
                    {
                        "handle": f"arch-{self._slate_n + i}",
                        "display_name": f"Archetype {self._slate_n + i}",
                        "background": "background",
                        "thesis": "thesis",
                        "specialties": ["backtesting"],
                        "differentiator": "different",
                        "weakness": "weak",
                        "axis": "rigor",
                    }
                    for i in range(want)
                ]
            }
            self._slate_n += want
        else:
            keys = list(schema["properties"]["scores"]["properties"])
            data = {
                "scores": {key: 7 for key in keys},
                "one_line": f"a read of {label}",
                "strengths": ["specific"],
                "concerns": ["vague in places"],
                "distinctive": "distinctive thing",
                "best_used_for": "backtest review",
                "interview_probe": "how would you detect look-ahead bias?",
            }
        return Result(text="{}", usage=USAGE, model=cfg.model, data=data)

    def research(self, stage, cfg, *, system, prompt, max_uses=12, max_restarts=6, label=""):
        self._record(stage, label)
        sources = [{"title": "Example", "url": "https://example.com/a"}]
        return Result(text="## Role reality\nIt is real.\n", usage=USAGE, model=cfg.model), sources

    def agent_loop(
        self, stage, cfg, *, system, prompt, tools, execute, max_turns=40, label="", on_step=None
    ):
        self._record(stage, label)
        # Behave like a candidate: write two files, then finish.
        execute("write_file", {"path": "strategy_tests.py", "content": "def test_x():\n    pass\n"})
        out, is_error = execute(
            "write_file",
            {"path": "SUBMISSION.md", "content": f"# Submission from {label}\n\nBuilt the thing.\n"},
        )
        transcript = [
            {"turn": 0, "type": "tool", "name": "write_file", "input": {"path": "strategy_tests.py"},
             "output": out, "is_error": is_error},
        ]
        return Result(text="done", usage=USAGE, model=cfg.model, stop_reason="end_turn"), transcript
