"""Rendering: leaderboards, status, cost summaries."""

from __future__ import annotations

from collections import defaultdict

from hire.config import PRICING
from hire.store import Opening, now_iso


def digest_md(
    opening_id: str,
    rnd: int,
    role: str,
    digests: dict[str, dict],
    comparison: str,
    advance_target: int,
) -> str:
    """The round's comparison document: the aggregate, then every digest."""
    lines = [
        f"# Round {rnd} comparison — {opening_id}",
        "",
        f"**Role:** {role}  ",
        f"**Generated:** {now_iso()}  ",
        f"**Answers digested:** {len(digests)}",
        "",
        "> Nothing here is a score or a ranking. These are the candidates' own",
        "> positions, extracted and grouped so you can see where the slate splits.",
        f"> You advance **{advance_target}**; read the answers of anyone this makes",
        "> look interesting, then run",
        f"> `hire shortlist {opening_id} --round {rnd} --advance <ids>`.",
        "",
        "---",
        "",
        comparison.strip(),
        "",
        "---",
        "",
        "## Every answer, digested",
        "",
    ]

    for cid in sorted(digests):
        d = digests[cid]
        lines += [
            f"### `{cid}` — {d.get('headline', '')}",
            "",
            f"**Their verdict:** {d.get('verdict', '—')}",
            "",
            f"**Only they said:** {d.get('distinctive', '—')}",
            "",
        ]
        for label, key in (
            ("Approach", "approach"),
            ("Specifics they named", "key_specifics"),
            ("Where they pushed back", "pushback"),
            ("What they need from you", "asks"),
            ("Risks they raised", "risks_named"),
            ("Not addressed", "not_addressed"),
        ):
            items = d.get(key) or []
            lines.append(f"**{label}**")
            lines += [f"- {item}" for item in items] or ["- (none recorded)"]
            lines.append("")
        lines += [f"**Stated uncertainty:** {d.get('stated_uncertainty', '—')}", ""]
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
        scored = opening.digests_path(rnd).exists()
        try:
            advanced = opening.advanced(rnd)
        except FileNotFoundError:
            advanced = []
        lines += [
            f"- [{'x' if candidates else ' '}] round {rnd} candidates: {len(candidates)}",
            f"- [{'x' if prompt.exists() else ' '}] round {rnd} "
            f"{'mock project prompt' if rnd == 1 else 'technical project'}",
            f"- [{'x' if responses else ' '}] round {rnd} interviews conducted: {len(responses)}",
            f"- [{'x' if scored else ' '}] round {rnd} digested",
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
    if not opening.digests_path(1).exists():
        return f"`hire digest {oid} --round 1`"
    try:
        opening.advanced(1)
    except FileNotFoundError:
        return (
            f"Read `openings/{oid}/round1/comparison.md` and the answers, then "
            f"`hire shortlist {oid} --round 1 --advance c07,c19,...`"
        )
    if not opening.candidate_ids(2):
        return f"`hire generate {oid} --round 2`"
    if not list(opening.responses_dir(2).glob("*.md")):
        return (
            f"Write your technical project, then "
            f"`hire round2 {oid} --project-file <file.md>`"
        )
    if not opening.digests_path(2).exists():
        return f"`hire digest {oid} --round 2`"
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
