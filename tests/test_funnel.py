"""Drive the whole funnel offline with a fake model."""

from __future__ import annotations

import json

import pytest

from fake_backend import FakeBackend
from hire.llm import LLM
from hire import finalize, pipeline
from hire.store import Opening, now_iso, read_doc


def make_opening(tmp_path, **funnel):
    opening = Opening("quant-qa", tmp_path)
    settings = {"round1_candidates": 6, "round1_advance": 2, "variants_per_winner": 2,
                "round2_advance": 2}
    settings.update(funnel)
    opening.save(
        {
            "id": "quant-qa",
            "role": "QA engineer for trading strategies",
            "brief": "Test backtests for look-ahead bias.",
            "qualifications": "pandas, pytest",
            "kb_role": "quant-qa",
            "created_at": now_iso(),
            "status": "open",
            "funnel": settings,
        }
    )
    return opening


def llm_for(opening):
    return LLM(backend=FakeBackend(), on_usage=opening.log_usage)


@pytest.fixture
def full_run(tmp_path):
    """Run the funnel end to end and hand back everything it produced."""
    opening = make_opening(tmp_path)
    backend = FakeBackend()
    llm = LLM(backend=backend, on_usage=opening.log_usage)

    pipeline.run_research(opening, llm)
    pipeline.generate_round1(opening, llm, count=6, concurrency=3)

    prompt = tmp_path / "mock.md"
    prompt.write_text("Build a walk-forward backtest harness for a momentum strategy.")
    pipeline.run_round1(opening, llm, prompt, concurrency=3)
    digests1 = pipeline.run_digest(opening, llm, 1, concurrency=3)

    winners = sorted(digests1)[:2]
    pipeline.record_decision(opening, 1, winners, general_notes="Liked the bias hunters.")

    pipeline.generate_round2(opening, llm, concurrency=3)
    project = tmp_path / "project.md"
    project.write_text("Write a test suite that catches look-ahead bias.")
    pipeline.run_round2(opening, llm, project, concurrency=2)
    digests2 = pipeline.run_digest(opening, llm, 2, concurrency=3)

    finalists = sorted(digests2)[:2]
    pipeline.record_decision(opening, 2, finalists, notes={finalists[0]: "Best artifacts."})
    finalize.run_round3(opening, llm)
    paths = finalize.hire_candidate(
        opening, llm, finalists[0], "backtest-qa", notes="Always report bias checks first."
    )
    return {"opening": opening, "llm": llm, "backend": backend, "winners": winners,
            "finalists": finalists, "paths": paths, "tmp": tmp_path}


def test_research_writes_findings_and_a_kb_entry(full_run):
    opening = full_run["opening"]
    findings = (opening.research_dir / "findings.md").read_text()
    assert "Role reality" in findings
    assert "https://example.com/a" in findings
    entry = (opening.root / "kb" / "entries" / "quant-qa-role-research.md").read_text()
    assert "role: quant-qa" in entry


def test_round1_generates_the_requested_number_of_distinct_candidates(full_run):
    opening = full_run["opening"]
    ids = opening.candidate_ids(1)
    assert ids == ["c01", "c02", "c03", "c04", "c05", "c06"]
    handles = {opening.load_candidate(1, cid).meta["handle"] for cid in ids}
    assert len(handles) == 6, "each candidate must get a distinct archetype"


def test_candidate_body_is_a_usable_system_prompt(full_run):
    doc = full_run["opening"].load_candidate(1, "c01")
    assert doc.body.startswith("## Positioning")
    assert "---" not in doc.body.splitlines()[0]
    assert doc.meta["role"] == "QA engineer for trading strategies"


def test_round1_interviews_every_candidate(full_run):
    opening = full_run["opening"]
    responses = sorted(p.stem for p in opening.responses_dir(1).glob("*.md"))
    assert responses == opening.candidate_ids(1)
    assert opening.prompt_path(1).read_text().startswith("Build a walk-forward")


def test_the_comparison_aggregates_without_scoring(full_run):
    opening = full_run["opening"]
    doc = opening.comparison_path(1).read_text()
    assert "hire shortlist" in doc
    assert "Nothing here is a score or a ranking" in doc
    # Each candidate's own content must survive into the comparison.
    for cid in opening.candidate_ids(1):
        assert f"`{cid}`" in doc
    assert "priced the hedge" in doc, "the distinctive field must reach the page"
    digests = json.loads(opening.digests_path(1).read_text())
    assert len(digests) == 6
    assert "scores" not in next(iter(digests.values()))


def test_nothing_in_the_pipeline_ranks_candidates(full_run):
    """The CEO ranks. The tool must not hand back an order dressed as a verdict."""
    doc = full_run["opening"].comparison_path(1).read_text().lower()
    for word in ("leaderboard", "/10", "weighted score", "top 5"):
        assert word not in doc


def test_the_aggregation_sees_every_digest(full_run):
    prompts_seen = full_run["backend"].prompts_for("aggregate")
    assert len(prompts_seen) == 2, "one aggregation per round"
    for cid in full_run["opening"].candidate_ids(1):
        assert cid in prompts_seen[0]


def test_round2_regenerates_variants_seeded_from_the_winners(full_run):
    opening, winners = full_run["opening"], full_run["winners"]
    ids = opening.candidate_ids(2)
    assert len(ids) == 4  # 2 winners x 2 variants
    for winner in winners:
        variants = [cid for cid in ids if cid.startswith(f"{winner}-v")]
        assert len(variants) == 2
        for cid in variants:
            meta = opening.load_candidate(2, cid).meta
            assert meta["parent"] == winner
            assert meta["axis"], "each variant is pushed along a named axis"


def test_variant_generation_is_seeded_with_the_parent_and_the_ceos_notes(tmp_path):
    """The round-1 winner markdown and the CEO's notes must reach the prompt."""
    opening = make_opening(tmp_path)
    backend = FakeBackend()
    llm = LLM(backend=backend, on_usage=opening.log_usage)
    pipeline.generate_round1(opening, llm, count=6, concurrency=2)
    prompt = tmp_path / "mock.md"
    prompt.write_text("mock project")
    pipeline.run_round1(opening, llm, prompt, concurrency=2)
    pipeline.record_decision(opening, 1, ["c01"], notes={"c01": "Push harder on data hygiene."})

    backend.specs.clear()
    pipeline.generate_round2(opening, llm, concurrency=1)
    seen = backend.prompts_for("candidate")
    assert seen, "round 2 must generate candidates"
    assert all("Lineage" in p for p in seen)
    assert all("Push harder on data hygiene." in p for p in seen)
    assert all("## Positioning" in p for p in seen), "parent system prompt is the foundation"


def test_round2_candidates_produce_real_artifacts(full_run):
    opening = full_run["opening"]
    for cid in opening.candidate_ids(2):
        workspace = opening.workspace(cid)
        assert (workspace / "SUBMISSION.md").is_file()
        assert (workspace / "strategy_tests.py").is_file()
        response = read_doc(opening.response_path(2, cid))
        assert response.meta["files_produced"] == 2
        assert response.meta["submitted"] is True
        assert "Submission from" in response.body


def test_round2_transcripts_are_saved(full_run):
    opening = full_run["opening"]
    for cid in opening.candidate_ids(2):
        path = opening.round_dir(2) / "transcripts" / f"{cid}.json"
        assert json.loads(path.read_text())[0]["name"] == "write_file"


def _to_round2(tmp_path, opening, llm):
    """Fast-forward an opening to the point where round 2 can run."""
    pipeline.generate_round1(opening, llm, count=2, concurrency=1)
    prompt = tmp_path / "p.md"
    prompt.write_text("p")
    pipeline.run_round1(opening, llm, prompt, concurrency=1)
    pipeline.record_decision(opening, 1, ["c01"])
    pipeline.generate_round2(opening, llm, concurrency=1)
    project = tmp_path / "proj.md"
    project.write_text("build it")
    return project


def test_exec_stays_disabled_unless_asked(tmp_path):
    opening = make_opening(tmp_path)
    backend = FakeBackend()
    llm = LLM(backend=backend, on_usage=opening.log_usage)
    project = _to_round2(tmp_path, opening, llm)

    pipeline.run_round2(opening, llm, project, concurrency=1)
    assert backend.builds, "round 2 must reach the backend"
    assert all(b["allow_exec"] is False for b in backend.builds)
    assert all("DISABLED" in b["prompt"] for b in backend.builds)


def test_exec_is_passed_through_when_enabled(tmp_path):
    opening = make_opening(tmp_path)
    backend = FakeBackend()
    llm = LLM(backend=backend, on_usage=opening.log_usage)
    project = _to_round2(tmp_path, opening, llm)

    pipeline.run_round2(opening, llm, project, concurrency=1, allow_exec=True)
    assert all(b["allow_exec"] is True for b in backend.builds)
    assert all("DISABLED" not in b["prompt"] for b in backend.builds)


def test_each_candidate_builds_in_its_own_workspace(tmp_path):
    opening = make_opening(tmp_path)
    backend = FakeBackend()
    llm = LLM(backend=backend, on_usage=opening.log_usage)
    project = _to_round2(tmp_path, opening, llm)

    pipeline.run_round2(opening, llm, project, concurrency=1)
    roots = {b["root"] for b in backend.builds}
    assert len(roots) == len(backend.builds), "workspaces must not be shared"
    for build in backend.builds:
        assert build["root"].name == build["label"]


def test_exec_brief_covers_every_finalist(full_run):
    opening, finalists = full_run["opening"], full_run["finalists"]
    brief = opening.brief_path.read_text()
    for cid in finalists:
        assert cid in brief
    assert "not the decision" in brief


def test_hired_agent_file_is_reusable(full_run):
    paths = full_run["paths"]
    doc = read_doc(paths["agent"])
    assert doc.meta["name"] == "backtest-qa"
    assert doc.meta["description"]
    assert doc.meta["hired_from"]["candidate"] == full_run["finalists"][0]
    assert doc.body.startswith("## Role")
    assert paths["skills"].is_dir()
    assert (paths["skills"] / "README.md").is_file()
    assert "Provenance" in paths["dossier"].read_text()


def test_hire_without_refine_keeps_the_candidate_text(tmp_path, full_run):
    opening, finalists = full_run["opening"], full_run["finalists"]
    finalize.hire_candidate(opening, None, finalists[1], "raw-agent", refine=False)
    doc = read_doc(opening.root / "agents" / "raw-agent.md")
    assert "## Positioning" in doc.body


def test_the_opening_records_the_hire(full_run):
    spec = full_run["opening"].load()
    assert spec["status"] == "hired"
    assert spec["hired"] == "backtest-qa"


def test_every_call_lands_in_the_cost_ledger(full_run):
    entries = full_run["opening"].ledger()
    stages = {e["stage"] for e in entries}
    assert {"research", "slate", "candidate", "round1", "round2", "digest",
            "aggregate", "brief", "refine"} <= stages
    assert len(entries) == len(full_run["backend"].calls)
    assert all(e["cost_usd"] > 0 for e in entries)


def test_a_failed_candidate_does_not_sink_the_round(tmp_path):
    opening = make_opening(tmp_path)
    backend = FakeBackend()
    llm = LLM(backend=backend, on_usage=opening.log_usage)
    original = backend.complete

    def flaky(spec):
        if spec.stage == "candidate" and spec.label == "c03":
            raise RuntimeError("simulated API failure")
        return original(spec)

    backend.complete = flaky
    made = pipeline.generate_round1(opening, llm, count=6, concurrency=3)
    assert "c03" not in made
    assert len(made) == 5
    assert set(opening.candidate_ids(1)) == set(made)


def test_shortlisting_an_unknown_candidate_is_refused(tmp_path):
    opening = make_opening(tmp_path)
    pipeline.generate_round1(opening, llm_for(opening), count=2, concurrency=1)
    with pytest.raises(RuntimeError, match="not round-1 candidates"):
        pipeline.record_decision(opening, 1, ["c01", "c99"])


def test_rounds_refuse_to_run_out_of_order(tmp_path):
    opening = make_opening(tmp_path)
    llm = llm_for(opening)
    prompt = tmp_path / "p.md"
    prompt.write_text("p")
    with pytest.raises(RuntimeError, match="generate"):
        pipeline.run_round1(opening, llm, prompt)
    with pytest.raises(FileNotFoundError, match="hire shortlist"):
        pipeline.generate_round2(opening, llm)


def test_an_empty_prompt_file_is_refused(tmp_path):
    opening = make_opening(tmp_path)
    llm = llm_for(opening)
    pipeline.generate_round1(opening, llm, count=2, concurrency=1)
    empty = tmp_path / "empty.md"
    empty.write_text("   \n")
    with pytest.raises(RuntimeError, match="empty"):
        pipeline.run_round1(opening, llm, empty)


def test_kb_material_reaches_candidate_generation(tmp_path):
    """House knowledge for the role must land in the candidates' own prompts."""
    from hire.kb import KnowledgeBase

    opening = make_opening(tmp_path)
    opening.update(kb_role="quant-qa")
    KnowledgeBase(tmp_path).add(
        "House execution assumptions", "We assume 1.5 ticks of slippage on stops.",
        role="quant-qa",
    )
    backend = FakeBackend()
    pipeline.generate_round1(opening, LLM(backend=backend), count=2, concurrency=1)
    assert all("1.5 ticks of slippage" in p for p in backend.prompts_for("candidate"))


def test_dry_run_makes_no_calls(tmp_path):
    opening = make_opening(tmp_path)
    backend = FakeBackend()
    pipeline.generate_round1(opening, None, count=50, dry_run=True)
    pipeline.run_research(opening, None, dry_run=True)
    assert opening.candidate_ids(1) == []
    assert backend.calls == []
