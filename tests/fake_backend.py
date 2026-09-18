"""A stand-in :class:`hire.backends.Backend` so the funnel can be tested offline.

Tests drive the real :class:`hire.llm.LLM` facade on top of this, so backend
dispatch, usage accounting and the empty-response checks are all exercised.
"""

from __future__ import annotations

import re

from hire.backends import Backend, BackendResult, CallSpec

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


class FakeBackend(Backend):
    """Deterministic content, plus a record of every call it received."""

    name = "fake"
    default_concurrency = 3

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.specs: list[CallSpec] = []
        self.builds: list[dict] = []
        self._slate_n = 0

    @staticmethod
    def available() -> bool:
        return True

    def _seen(self, spec: CallSpec) -> None:
        self.calls.append((spec.stage, spec.label))
        self.specs.append(spec)

    def prompts_for(self, stage: str) -> list[str]:
        return [s.prompt for s in self.specs if s.stage == stage]

    def complete(self, spec: CallSpec) -> BackendResult:
        self._seen(spec)
        if spec.stage == "candidate":
            text = CANDIDATE_BODY
        elif spec.stage == "refine":
            text = "## Role\nYou are the hired agent.\n"
        else:
            text = f"# {spec.stage} output for {spec.label}\n\nbody"
        return BackendResult(text=text, usage=dict(USAGE), model="fake-model", cost_usd=0.01)

    def complete_json(self, spec: CallSpec, schema: dict) -> BackendResult:
        self._seen(spec)
        if spec.stage == "slate":
            match = re.search(r"Design (\d+) distinct", spec.prompt)
            want = int(match.group(1)) if match else 10
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
                "one_line": f"a read of {spec.label}",
                "strengths": ["specific"],
                "concerns": ["vague in places"],
                "distinctive": "distinctive thing",
                "best_used_for": "backtest review",
                "interview_probe": "how would you detect look-ahead bias?",
            }
        return BackendResult(
            text="{}", usage=dict(USAGE), model="fake-model", cost_usd=0.01, data=data
        )

    def research(self, spec: CallSpec, max_uses: int):
        self._seen(spec)
        sources = [{"title": "Example", "url": "https://example.com/a"}]
        result = BackendResult(
            text="## Role reality\nIt is real.\n",
            usage=dict(USAGE),
            model="fake-model",
            cost_usd=0.01,
        )
        return result, sources

    def build(self, spec: CallSpec, workspace, allow_exec: bool, max_turns: int):
        self._seen(spec)
        self.builds.append(
            {"label": spec.label, "allow_exec": allow_exec, "prompt": spec.prompt,
             "root": workspace.root}
        )
        # Behave like a candidate working in its workspace.
        workspace.execute("write_file", {"path": "strategy_tests.py", "content": "def test_x():\n    pass\n"})
        out, is_error = workspace.execute(
            "write_file",
            {"path": "SUBMISSION.md", "content": f"# Submission from {spec.label}\n\nBuilt the thing.\n"},
        )
        transcript = [
            {"turn": 0, "type": "tool", "name": "write_file",
             "input": {"path": "strategy_tests.py"}, "output": out, "is_error": is_error},
        ]
        result = BackendResult(
            text="done", usage=dict(USAGE), model="fake-model", cost_usd=0.01,
            stop_reason="end_turn",
        )
        return result, transcript
