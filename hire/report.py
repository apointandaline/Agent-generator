"""Rendering: leaderboards, status, cost summaries."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from hire.config import PRICING
from hire.store import Opening, now_iso


def weighted_score(scores: dict, rubric: Sequence[dict]) -> float:
    """Rubric-weighted mean on a 1-10 scale. Missing lines are skipped."""
    total_weight = 0.0
    total = 0.0
    for item in rubric:
        if item["key"] in scores:
            weight = float(item["weight"])
            total += float(scores[item["key"]]) * weight
            total_weight += weight
    return round(total / total_weight, 2) if total_weight else 0.0


def rank(scorecards: dict[str, dict], rubric: Sequence[dict]) -> list[tuple[str, float, dict]]:
    ranked = [
        (cid, weighted_score(card.get("scores", {}), rubric), card)
        for cid, card in scorecards.items()
    ]
    return sorted(ranked, key=lambda row: row[1], reverse=True)


def leaderboard_md(
    opening_id: str,
    rnd: int,
    role: str,
    scorecards: dict[str, dict],
    rubric: Sequence[dict],
    advance_target: int,
) -> str:
    ranked = rank(scorecards, rubric)
    keys = [item["key"] for item in rubric]
    header = "| # | candidate | score | " + " | ".join(keys) + " | one-line read |"
    divider = "|---" * (4 + len(keys)) + "|"
    rows = []
    for index, (cid, score, card) in enumerate(ranked, start=1):
        scores = card.get("scores", {})
        cells = " | ".join(str(scores.get(key, "–")) for key in keys)
        one_line = str(card.get("one_line", "")).replace("|", "/").strip()
        rows.append(f"| {index} | `{cid}` | **{score}** | {cells} | {one_line} |")

    lines = [
        f"# Round {rnd} leaderboard — {opening_id}",
        "",
        f"**Role:** {role}  ",
        f"**Generated:** {now_iso()}  ",
        f"**Candidates assessed:** {len(ranked)}",
        "",
        "> These scores are **advisory**. They exist so you can triage a large slate,",
        "> not to pick for you. Read the top candidates' actual answers before deciding,",
        f"> then record your picks with `hire shortlist {opening_id} --round {rnd} --advance <ids>`.",
        f"> You said you want to advance **{advance_target}** from this round.",
        "",
        header,
        divider,
        *rows,
        "",
        "---",
        "",
        "## Candidate detail",
        "",
    ]

    for index, (cid, score, card) in enumerate(ranked, start=1):
        strengths = [f"- {s}" for s in card.get("strengths", [])] or ["- (none recorded)"]
        concerns = [f"- {c}" for c in card.get("concerns", [])] or ["- (none recorded)"]
        lines += [
            f"### {index}. `{cid}` — {score}",
            "",
            f"*{card.get('one_line', '')}*",
            "",
            f"**Distinctive:** {card.get('distinctive', '—')}",
            "",
            f"**Best used for:** {card.get('best_used_for', '—')}",
            "",
            "**Strengths**",
            *strengths,
            "",
            "**Concerns**",
            *concerns,
            "",
            f"**Probe to ask:** {card.get('interview_probe', '—')}",
            "",
        ]
    return "\n".join(lines) + "\n"


def cost_summary(opening: Opening) -> str:
    entries = opening.ledger()
    if not entries:
        return f"No API spend recorded for '{opening.id}' yet."

    by_stage: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
    unpriced: set[str] = set()
    for entry in entries:
        bucket = by_stage[entry["stage"]]
        bucket["calls"] += 1
        bucket["in"] += entry["usage"].get("input_tokens", 0)
        bucket["out"] += entry["usage"].get("output_tokens", 0)
        bucket["usd"] += entry.get("cost_usd", 0.0)
        if entry["model"] not in PRICING:
            unpriced.add(entry["model"])

    lines = [
        f"# Spend — {opening.id}",
        "",
        "| stage | calls | input tokens | output tokens | est. USD |",
        "|---|---|---|---|---|",
    ]
    total_calls = total_usd = 0
    for stage, bucket in sorted(by_stage.items(), key=lambda kv: kv[1]["usd"], reverse=True):
        lines.append(
            f"| {stage} | {bucket['calls']} | {bucket['in']:,} | {bucket['out']:,} | ${bucket['usd']:.2f} |"
        )
        total_calls += bucket["calls"]
        total_usd += bucket["usd"]
    lines += [
        f"| **total** | **{total_calls}** | | | **${total_usd:.2f}** |",
        "",
        "Costs are estimated locally from a cached price table and are not a bill.",
    ]
    if unpriced:
        lines.append(f"No price on file for: {', '.join(sorted(unpriced))} — those calls count as $0.")
    return "\n".join(lines)


def status_report(opening: Opening) -> str:
    spec = opening.load()
    settings = opening.settings()
    funnel = settings.funnel
    lines = [
        f"# {opening.id} — {spec.get('role', '(no role)')}",
        "",
        f"Created {spec.get('created_at', '?')}, updated {spec.get('updated_at', '?')}",
        "",
        "## Funnel",
        f"- Round 1: generate {funnel.round1_candidates}, advance {funnel.round1_advance}",
        f"- Round 2: {funnel.variants_per_winner} variants per winner = "
        f"{funnel.round2_candidates}, advance {funnel.round2_advance}",
        "- Round 3: executive brief, hire 1",
        "",
        "## Progress",
    ]

    research = opening.research_dir / "findings.md"
    lines.append(f"- [{'x' if research.exists() else ' '}] research")

    for rnd in (1, 2):
        candidates = opening.candidate_ids(rnd)
        responses = list(opening.responses_dir(rnd).glob("*.md")) if opening.responses_dir(rnd).exists() else []
        prompt = opening.prompt_path(rnd)
        scored = opening.scorecards_path(rnd).exists()
        try:
            advanced = opening.advanced(rnd)
        except FileNotFoundError:
            advanced = []
        lines += [
            f"- [{'x' if candidates else ' '}] round {rnd} candidates: {len(candidates)}",
            f"- [{'x' if prompt.exists() else ' '}] round {rnd} "
            f"{'mock project prompt' if rnd == 1 else 'technical project'}",
            f"- [{'x' if responses else ' '}] round {rnd} interviews conducted: {len(responses)}",
            f"- [{'x' if scored else ' '}] round {rnd} screened",
            f"- [{'x' if advanced else ' '}] round {rnd} CEO decision: "
            + (", ".join(f"`{a}`" for a in advanced) if advanced else "pending"),
        ]

    lines.append(f"- [{'x' if opening.brief_path.exists() else ' '}] round 3 executive brief")
    hired = spec.get("hired")
    lines.append(f"- [x] hired: `{hired}`" if hired else "- [ ] hired: pending")

    lines += ["", "## Next step", f"- {next_step(opening)}"]
    return "\n".join(lines) + "\n"


def next_step(opening: Opening) -> str:
    """The single command the CEO should run next."""
    oid = opening.id
    spec = opening.load()
    if spec.get("hired"):
        return f"Done — hired agent is in `agents/{spec['hired']}.md`."
    if not (opening.research_dir / "findings.md").exists():
        return f"`hire research {oid}`"
    if not opening.candidate_ids(1):
        return f"`hire generate {oid} --round 1`"
    if not opening.prompt_path(1).exists():
        return (
            f"Write your mock project prompt, then "
            f"`hire round1 {oid} --prompt-file <file.md>`"
        )
    if not list(opening.responses_dir(1).glob("*.md")):
        return f"`hire round1 {oid} --prompt-file <file.md>`"
    if not opening.scorecards_path(1).exists():
        return f"`hire screen {oid} --round 1`"
    try:
        opening.advanced(1)
    except FileNotFoundError:
        return (
            f"Read `openings/{oid}/round1/leaderboard.md` and the answers, then "
            f"`hire shortlist {oid} --round 1 --advance c07,c19,...`"
        )
    if not opening.candidate_ids(2):
        return f"`hire generate {oid} --round 2`"
    if not list(opening.responses_dir(2).glob("*.md")):
        return (
            f"Write your technical project, then "
            f"`hire round2 {oid} --project-file <file.md>`"
        )
    if not opening.scorecards_path(2).exists():
        return f"`hire screen {oid} --round 2`"
    try:
        opening.advanced(2)
    except FileNotFoundError:
        return (
            f"Review round-2 workspaces, then "
            f"`hire shortlist {oid} --round 2 --advance <ids>`"
        )
    if not opening.brief_path.exists():
        return f"`hire round3 {oid}`"
    return f"Read the brief, then `hire hire {oid} --candidate <id> --name <agent-name>`"
