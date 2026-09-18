"""Local knowledge base.

Entries are markdown files with YAML frontmatter under ``kb/entries/``. The
index is derived from those files on every read, so adding an entry by hand —
dropping a file in with frontmatter, or editing one the researcher wrote — is a
first-class way to use this. Nothing is hidden in a database.

Entries are tagged by ``role``, which is how candidate generation picks up the
right background material for an opening.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hire.store import Doc, now_iso, read_doc, repo_root, slugify



@dataclass
class Entry:
    id: str
    path: Path
    meta: dict
    body: str

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.id

    @property
    def role(self) -> str:
        return self.meta.get("role") or "general"

    @property
    def tags(self) -> list[str]:
        return list(self.meta.get("tags") or [])

    @property
    def source(self) -> str:
        return self.meta.get("source") or ""

    def summary(self, width: int = 100) -> str:
        first = next((ln.strip() for ln in self.body.splitlines() if ln.strip() and not ln.startswith("#")), "")
        return first[:width] + ("…" if len(first) > width else "")




class KnowledgeBase:
    def __init__(self, root: Path | None = None):
        self.root = (root or repo_root()).resolve()
        self.dir = self.root / "kb" / "entries"

    def entry_path(self, entry_id: str) -> Path:
        return self.dir / f"{entry_id}.md"

    def all(self) -> list[Entry]:
        if not self.dir.exists():
            return []
        entries = []
        for path in sorted(self.dir.glob("*.md")):
            doc = read_doc(path)
            entries.append(Entry(id=path.stem, path=path, meta=doc.meta, body=doc.body))
        return entries

    def list(self, role: str | None = None, tag: str | None = None) -> list[Entry]:
        """Entries filtered by role and/or tag. ``role`` also matches 'general'."""
        out = self.all()
        if role:
            wanted = role.lower()
            out = [e for e in out if e.role.lower() in (wanted, "general")]
        if tag:
            out = [e for e in out if tag.lower() in [t.lower() for t in e.tags]]
        return out

    def get(self, entry_id: str) -> Entry:
        path = self.entry_path(entry_id)
        if not path.exists():
            raise FileNotFoundError(f"no knowledge-base entry '{entry_id}' at {path}")
        doc = read_doc(path)
        return Entry(id=entry_id, path=path, meta=doc.meta, body=doc.body)

    def search(self, query: str, role: str | None = None) -> list[tuple[Entry, int]]:
        """Case-insensitive term search over title, tags and body, ranked by hits."""
        terms = [t for t in re.split(r"\s+", query.lower()) if t]
        scored: list[tuple[Entry, int]] = []
        for entry in self.list(role=role):
            haystack = f"{entry.title}\n{' '.join(entry.tags)}\n{entry.body}".lower()
            hits = sum(haystack.count(term) for term in terms)
            if hits:
                scored.append((entry, hits))
        return sorted(scored, key=lambda pair: pair[1], reverse=True)

    def add(
        self,
        title: str,
        body: str,
        *,
        role: str = "general",
        tags: Iterable[str] = (),
        source: str = "",
        origin: str = "manual",
        entry_id: str | None = None,
        sources: list[dict] | None = None,
    ) -> Entry:
        """Write a new entry (or overwrite one with the same id)."""
        entry_id = entry_id or slugify(f"{role}-{title}")
        meta = {
            "title": title,
            "role": role,
            "tags": sorted({t.strip() for t in tags if t.strip()}),
            "source": source,
            "origin": origin,
            "added_at": now_iso(),
        }
        if sources:
            meta["sources"] = sources
        path = Doc(meta=meta, body=body).write(self.entry_path(entry_id))
        return Entry(id=entry_id, path=path, meta=meta, body=body.strip())


    def context_for(self, role: str, max_chars: int = 24_000, limit: int = 12) -> str:
        """Assemble KB material for a role into a prompt block.

        Returns an empty string when the KB has nothing for the role, which
        callers treat as "generate from model knowledge and say so".
        """
        entries = self.list(role=role)
        if not entries:
            return ""
        chunks: list[str] = []
        used = 0
        for entry in entries[:limit]:
            header = f"### KB[{entry.id}] {entry.title}"
            if entry.source:
                header += f"\nsource: {entry.source}"
            chunk = f"{header}\n\n{entry.body.strip()}"
            if used + len(chunk) > max_chars:
                remaining = max_chars - used
                if remaining < 500:
                    break
                chunk = chunk[:remaining] + "\n…[truncated]"
            chunks.append(chunk)
            used += len(chunk)
            if used >= max_chars:
                break
        return "\n\n".join(chunks)
