"""Round 3 (executive brief) and the final hire."""

from __future__ import annotations

import json
from pathlib import Path

from hire import prompts
from hire.llm import LLM, log
from hire.pipeline import estimate
from hire.store import Doc, Opening, now_iso, read_doc, slugify, write_text
from hire.workspace import Workspace


def _digests(opening: Opening, rnd: int) -> dict:
    path = opening.digests_path(rnd)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _digest_lines(digest: dict) -> list[str]:
    """Render one candidate's digest for the brief, without adding a judgement."""
    if not digest:
        return ["- (no digest on file)"]
    out = [f"- verdict: {digest.get('verdict', '—')}",
           f"- only they said: {digest.get('distinctive', '—')}"]
    for label, key in (
        ("pushed back on", "pushback"),
        ("asked for", "asks"),
        ("risks they raised", "risks_named"),
        ("did not address", "not_addressed"),
    ):
        items = digest.get(key) or []
        out.append(f"- {label}: {'; '.join(items) if items else '—'}")
    out.append(f"- stated uncertainty: {digest.get('stated_uncertainty', '—')}")
    return out


def _round1_ancestor(opening: Opening, candidate_id: str) -> str:
    """Round-2 ids are '<parent>-v<n>'; recover the parent's round-1 answer."""
    doc = opening.load_candidate(2, candidate_id)
    parent = doc.meta.get("parent")
    if not parent:
        return ""
    path = opening.response_path(1, parent)
    return read_doc(path).body if path.exists() else ""


def build_dossiers(opening: Opening, finalists: list[str], per_section: int = 9_000) -> str:
    """Assemble everything known about each finalist for the brief."""
    digests1 = _digests(opening, 1)
    digests2 = _digests(opening, 2)
    chunks: list[str] = []

    for cid in finalists:
        doc = opening.load_candidate(2, cid)
        parent = doc.meta.get("parent", "")
        r1_answer = _round1_ancestor(opening, cid)[:per_section]
        r2_path = opening.response_path(2, cid)
        r2_submission = read_doc(r2_path).body[:per_section] if r2_path.exists() else ""
        ws_dir = opening.workspace(cid)
        tree = Workspace(ws_dir).list() if ws_dir.exists() else "(no workspace)"

        digest1 = digests1.get(parent, {})
        digest2 = digests2.get(cid, {})

        chunks.append(
            "\n".join(
                [
                    f"## Finalist `{cid}`",
                    f"- display name: {doc.meta.get('display_name', cid)}",
                    f"- descended from round-1 candidate: `{parent or 'unknown'}`",
                    f"- specialisation axis: {doc.meta.get('axis', '—')}",
                    f"- declared specialties: {', '.join(doc.meta.get('specialties', [])) or '—'}",
                    f"- self-declared weakness: {doc.meta.get('declared_weakness', '—')}",
                    "",
                    f"### Round-1 digest (as `{parent}`)",
                    *_digest_lines(digest1),
                    "",
                    "### Round-2 digest",
                    *_digest_lines(digest2),
                    "",
                    "### Its system prompt",
                    doc.body[:per_section],
                    "",
                    "### Round-1 answer (inherited from its parent)",
                    r1_answer or "(not available)",
                    "",
                    "### Round-2 submission",
                    r2_submission or "(not available)",
                    "",
                    "### Round-2 workspace contents",
                    "```",
                    tree,
                    "```",
                    "",

                ]
            )
        )
    return "\n".join(chunks)


def run_round3(opening: Opening, llm: LLM, *, dry_run: bool = False) -> Path | None:
    spec = opening.load()
    cfg = opening.settings().stage("brief")
    finalists = opening.advanced(2)
    if not finalists:
        raise RuntimeError("round 2 decision records no finalists")
    if dry_run:
        log(f"[dry-run] round 3: 1 brief call over {len(finalists)} finalists, "
            f"~${estimate('brief', 1, cfg.model):.2f}")
        return None

    log(f"Round 3: building the executive brief over {len(finalists)} finalists…")
    result = llm.text(
        "brief",
        cfg,
        system=prompts.BRIEF_SYSTEM,
        prompt=prompts.brief_prompt(
            spec["role"], spec.get("brief", ""), build_dossiers(opening, finalists)
        ),
        label=opening.id,
    )

    header = "\n".join(
        [
            f"# Executive brief — {spec['role']}",
            "",
            f"Opening `{opening.id}` · {now_iso()}",
            "",
            f"**Finalists:** {', '.join(f'`{f}`' for f in finalists)}",
            "",
            "> This brief is preparation for your decision, not the decision. When you have",
            f"> chosen, run `hire hire {opening.id} --candidate <id> --name <agent-name>`.",
            "",
            "---",
            "",
        ]
    )
    path = write_text(opening.brief_path, header + result.text + "\n")
    opening.update(status="round3-brief-ready")
    log(f"→ {path}")
    return path


# --------------------------------------------------------------------------- #
# The hire
# --------------------------------------------------------------------------- #


def hire_candidate(
    opening: Opening,
    llm: LLM | None,
    candidate_id: str,
    agent_name: str | None = None,
    *,
    notes: str = "",
    refine: bool = True,
    description: str = "",
    dry_run: bool = False,
) -> dict[str, Path]:
    """Emit the hired agent: a system prompt, a dossier and a skills directory."""
    spec = opening.load()
    settings = opening.settings()
    doc = opening.load_candidate(2, candidate_id)
    name = slugify(agent_name or doc.meta.get("handle") or f"{opening.id}-agent")
    agents_dir = opening.root / "agents"
    agent_file = agents_dir / f"{name}.md"
    skills_dir = agents_dir / name / "skills"
    skills_rel = f"agents/{name}/skills/"

    r2_path = opening.response_path(2, candidate_id)
    r2_submission = read_doc(r2_path).body if r2_path.exists() else ""
    r1_answer = _round1_ancestor(opening, candidate_id)

    if dry_run:
        cfg = settings.stage("refine")
        cost = estimate("refine", 1, cfg.model) if refine else 0.0
        log(f"[dry-run] hire {candidate_id} → agents/{name}.md ({'1 refine call, ' if refine else ''}~${cost:.2f})")
        return {}

    body = doc.body
    if refine:
        if llm is None:
            raise RuntimeError("refinement needs an API client; pass --no-refine to skip it")
        log(f"Refining {candidate_id} into its hired form…")
        result = llm.text(
            "refine",
            settings.stage("refine"),
            system=prompts.REFINE_SYSTEM,
            prompt=prompts.refine_prompt(
                spec["role"], spec.get("brief", ""), doc.body,
                r1_answer, r2_submission, notes, skills_rel,
            ),
            label=name,
        )
        body = result.text

    # Frontmatter is Claude Code subagent-compatible: a `name`/`description` pair
    # plus an optional model, so the file can be dropped into .claude/agents/.
    meta = {
        "name": name,
        "description": description
        or (doc.meta.get("thesis") or f"{spec['role']} — hired via opening {opening.id}"),
        "model": settings.stage("refine").model,
        "role": spec["role"],
        "hired_from": {
            "opening": opening.id,
            "candidate": candidate_id,
            "round1_ancestor": doc.meta.get("parent", ""),
            "hired_at": now_iso(),
        },
        "specialties": doc.meta.get("specialties", []),
        "skills_dir": skills_rel,
    }
    written = Doc(meta=meta, body=body).write(agent_file)

    skills_dir.mkdir(parents=True, exist_ok=True)
    readme = skills_dir / "README.md"
    if not readme.exists():
        write_text(
            readme,
            "\n".join(
                [
                    f"# Skills for `{name}`",
                    "",
                    "Drop one markdown file per skill in this directory. A skill is a",
                    "procedure this agent should follow for a particular kind of task —",
                    "keep each one self-contained and concrete.",
                    "",
                    "Suggested shape:",
                    "",
                    "```markdown",
                    "---",
                    "name: backtest-review",
                    "description: Review a backtest for look-ahead bias and overfitting.",
                    "---",
                    "",
                    "## When to use this",
                    "## Steps",
                    "## What to report",
                    "```",
                    "",
                    "The agent's instructions tell it to read the relevant file here",
                    "before doing work a skill covers.",
                    "",
                ]
            ),
        )

    dossier = _dossier(opening, candidate_id, doc, name, notes)
    dossier_path = write_text(agents_dir / name / "dossier.md", dossier)

    opening.update(status="hired", hired=name, hired_candidate=candidate_id)
    log(f"→ {written}")
    log(f"→ {dossier_path}")
    log(f"→ {skills_dir}/ (add skills here)")
    return {"agent": written, "dossier": dossier_path, "skills": skills_dir}


def _dossier(opening: Opening, candidate_id: str, doc: Doc, name: str, notes: str) -> str:
    settings = opening.settings()
    digests1, digests2 = _digests(opening, 1), _digests(opening, 2)
    parent = doc.meta.get("parent", "")
    digest1, digest2 = digests1.get(parent, {}), digests2.get(candidate_id, {})
    funnel = settings.funnel

    return "\n".join(
        [
            f"# Hiring dossier — `{name}`",
            "",
            f"Hired {now_iso()} from opening `{opening.id}`.",
            "",
            "## Provenance",
            f"- Round 1: one of {funnel.round1_candidates} candidates; advanced as `{parent or '—'}`",
            f"- Round 2: regenerated as `{candidate_id}` along the axis "
            f"*{doc.meta.get('axis', '—')}*; one of {funnel.round2_candidates}",
            f"- Round 3: chosen from {len(opening.advanced(2))} finalists by the CEO",
            "",
            "## What it said in the interviews",
            f"- Round 1 verdict (as `{parent}`): {digest1.get('verdict', '—')}",
            f"- Round 2 verdict: {digest2.get('verdict', '—')}",
            f"- Distinctive to this candidate: {digest2.get('distinctive', '—')}",
            "",
            "## What to supervise",
            f"- Did not address: {'; '.join(digest2.get('not_addressed', [])) or '—'}",
            f"- Its own stated uncertainty: {digest2.get('stated_uncertainty', '—')}",
            f"- Self-declared weakness: {doc.meta.get('declared_weakness', '—')}",
            "",
            "## CEO's notes at hire",
            notes or "(none)",
            "",
            "## Where to look",
            f"- Interview candidate file: `openings/{opening.id}/round2/candidates/{candidate_id}.md`",
            f"- Round-1 answer: `openings/{opening.id}/round1/responses/{parent}.md`",
            f"- Round-2 submission: `openings/{opening.id}/round2/responses/{candidate_id}.md`",
            f"- Round-2 artifacts: `openings/{opening.id}/round2/workspaces/{candidate_id}/`",
            f"- Round-1 comparison: `openings/{opening.id}/round1/comparison.md`",
            f"- Executive brief: `openings/{opening.id}/round3/exec-brief.md`",
            "",
        ]
    )
