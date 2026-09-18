import pytest

from hire.kb import KnowledgeBase
from hire.store import Doc, Opening, parse_doc, slugify


def test_frontmatter_round_trip():
    doc = Doc(meta={"id": "c01", "tags": ["a", "b"]}, body="# Body\n\ntext")
    parsed = parse_doc(doc.render())
    assert parsed.meta == {"id": "c01", "tags": ["a", "b"]}
    assert parsed.body == "# Body\n\ntext"


def test_body_without_frontmatter_is_kept():
    assert parse_doc("just a body").body == "just a body"
    assert parse_doc("just a body").meta == {}


def test_frontmatter_must_be_a_mapping():
    with pytest.raises(ValueError):
        parse_doc("---\n- a\n- b\n---\nbody")


def test_slugify():
    assert slugify("QA for Trading Strategies!") == "qa-for-trading-strategies"
    assert slugify("!!!") == "untitled"


def test_missing_opening_explains_itself(tmp_path):
    with pytest.raises(FileNotFoundError, match="hire open"):
        Opening("nope", tmp_path).load()


def test_kb_add_list_search(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.add("Backtest pitfalls", "Look-ahead bias kills backtests.", role="qa", tags=["bias"])
    kb.add("PM basics", "Write the spec first.", role="pm")
    kb.add("Shared", "Applies everywhere.", role="general")

    assert {e.id for e in kb.list(role="qa")} == {"qa-backtest-pitfalls", "general-shared"}
    assert [e.id for e, _ in kb.search("look-ahead")] == ["qa-backtest-pitfalls"]
    assert kb.list(tag="bias")[0].role == "qa"


def test_kb_context_is_empty_when_role_unknown(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.add("PM basics", "Write the spec first.", role="pm")
    assert kb.context_for("qa") == ""
    assert "spec first" in kb.context_for("pm")


def test_kb_context_respects_budget(tmp_path):
    kb = KnowledgeBase(tmp_path)
    for i in range(10):
        kb.add(f"Entry {i}", "x" * 5000, role="qa")
    assert len(kb.context_for("qa", max_chars=8000)) <= 8200


def test_hand_written_entry_is_picked_up(tmp_path):
    """Dropping a file in by hand is a supported way to extend the KB."""
    kb = KnowledgeBase(tmp_path)
    kb.dir.mkdir(parents=True)
    (kb.dir / "my-notes.md").write_text(
        "---\ntitle: My notes\nrole: qa\ntags: [manual]\n---\n\nUse walk-forward validation.\n"
    )
    entry = kb.get("my-notes")
    assert entry.role == "qa"
    assert "walk-forward" in kb.context_for("qa")
