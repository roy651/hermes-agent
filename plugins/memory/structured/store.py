"""File I/O for structured memory: long-term per-skill/infra files and short-term session store."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENTRY_SEP = "\n---\n"
DATE_FMT = "%Y-%m-%d"


class StructuredStore:
    def __init__(self, base: Path):
        self.base = base
        self.long_term = base / "long_term"
        self.sessions_dir = base / "short_term" / "sessions"
        self._lock = threading.Lock()
        self._init_dirs()

    def _init_dirs(self) -> None:
        for d in (self.long_term / "skills", self.long_term / "infra", self.sessions_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── Long-term: path helpers ──────────────────────────────────────────────

    def _lt_path(self, target: str) -> Path:
        """'skills/navman' or 'infra/gateway' → absolute .md path."""
        parts = target.strip("/").split("/", 1)
        category, name = (parts[0], parts[1]) if len(parts) == 2 else ("infra", parts[0])
        return self.long_term / category / f"{name}.md"

    # ── Long-term: entry parsing ─────────────────────────────────────────────

    def _parse_entries(self, text: str) -> Tuple[str, List[str]]:
        """Split a long-term file into (header, [entry, ...]).

        Header is the # title line (and optional description).
        Each entry is one typed block (type/date/content/why/apply).
        """
        chunks = text.split("\n---\n")
        first = chunks[0]

        # The first chunk may contain the header + first entry, or just the header.
        m = re.search(r"\ntype:", first)
        if m:
            header = first[: m.start()]
            entries = [first[m.start() + 1 :]] + [c.strip("\n") for c in chunks[1:]]
        else:
            header = first
            entries = [c.strip("\n") for c in chunks[1:]]

        return header, [e for e in entries if e.strip()]

    def _reconstruct(self, header: str, entries: List[str]) -> str:
        """Rebuild file text from header + entry list."""
        parts = [header.rstrip()] + [e.strip() for e in entries if e.strip()]
        if len(parts) == 1:
            return parts[0] + "\n"
        result = parts[0] + "\n\n" + parts[1]
        for part in parts[2:]:
            result = result.rstrip() + "\n\n---\n\n" + part
        return result.rstrip() + "\n"

    def _make_block(self, entry: Dict[str, str]) -> str:
        date = datetime.now(timezone.utc).strftime(DATE_FMT)
        return (
            f"type: {entry['type']}\n"
            f"date: {date}\n"
            f"content: {entry['content']}\n"
            f"why: {entry['why']}\n"
            f"apply: {entry['apply']}\n"
        )

    # ── Long-term: CRUD ──────────────────────────────────────────────────────

    def read_long_term(self, target: str) -> str:
        path = self._lt_path(target)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_long_term_entry(self, target: str, entry: Dict[str, str]) -> None:
        path = self._lt_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        block = self._make_block(entry)
        with self._lock:
            if path.exists():
                existing = path.read_text(encoding="utf-8")
                path.write_text(existing.rstrip() + f"\n{ENTRY_SEP}\n{block}", encoding="utf-8")
            else:
                name = target.split("/")[-1]
                path.write_text(f"# {name}\n\n{block}", encoding="utf-8")

    def replace_long_term_entry(
        self, target: str, match: str, new_entry: Dict[str, str]
    ) -> Tuple[bool, str]:
        """Replace the entry whose content contains `match` with `new_entry`.

        Returns (ok, message). Fails if 0 or 2+ entries match (ambiguous).
        """
        path = self._lt_path(target)
        if not path.exists():
            return False, f"File '{target}' not found."
        with self._lock:
            text = path.read_text(encoding="utf-8")
            header, entries = self._parse_entries(text)
            hits = [i for i, e in enumerate(entries) if match.lower() in e.lower()]
            if not hits:
                return False, f"No entry found containing: {match!r}"
            if len(hits) > 1:
                return False, (
                    f"{len(hits)} entries match {match!r} — use a more specific substring."
                )
            entries[hits[0]] = self._make_block(new_entry)
            path.write_text(self._reconstruct(header, entries), encoding="utf-8")
        return True, f"Replaced entry in '{target}'."

    def remove_long_term_entry(self, target: str, match: str) -> Tuple[bool, str]:
        """Remove the entry whose content contains `match`.

        Returns (ok, message). Fails if 0 or 2+ entries match.
        """
        path = self._lt_path(target)
        if not path.exists():
            return False, f"File '{target}' not found."
        with self._lock:
            text = path.read_text(encoding="utf-8")
            header, entries = self._parse_entries(text)
            hits = [i for i, e in enumerate(entries) if match.lower() in e.lower()]
            if not hits:
                return False, f"No entry found containing: {match!r}"
            if len(hits) > 1:
                return False, (
                    f"{len(hits)} entries match {match!r} — use a more specific substring."
                )
            removed = entries.pop(hits[0])
            path.write_text(self._reconstruct(header, entries), encoding="utf-8")
        preview = removed.strip()[:80]
        return True, f"Removed entry from '{target}': {preview}..."

    def create_long_term_file(self, target: str, description: str = "") -> str:
        path = self._lt_path(target)
        if path.exists():
            return f"File '{target}' already exists."
        path.parent.mkdir(parents=True, exist_ok=True)
        name = target.split("/")[-1]
        header = f"# {name}\n"
        if description:
            header += f"_{description}_\n"
        path.write_text(header + "\n", encoding="utf-8")
        return f"Created '{target}'."

    def list_long_term_files(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for cat_dir in sorted(self.long_term.iterdir()):
            if cat_dir.is_dir():
                result[cat_dir.name] = sorted(f.stem for f in cat_dir.glob("*.md"))
        return result

    # ── Short-term sessions ──────────────────────────────────────────────────

    def write_session(self, session_id: str, data: Dict[str, Any]) -> None:
        path = self.sessions_dir / f"{session_id}.json"
        with self._lock:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_recent_sessions(self, topics: List[str], limit: int = 2) -> List[Dict[str, Any]]:
        """Recent non-archived sessions that share any of the given topic targets."""
        results: List[Dict[str, Any]] = []
        topic_set = set(topics)
        paths = sorted(
            self.sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for p in paths:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("status") == "archived":
                continue
            if not topic_set or topic_set.intersection(data.get("topics", [])):
                results.append(data)
                if len(results) >= limit:
                    break
        return results

    def get_pending_sessions(self, min_age_hours: int = 24) -> List[Dict[str, Any]]:
        """Sessions not yet distilled that are older than min_age_hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
        results: List[Dict[str, Any]] = []
        for p in sorted(self.sessions_dir.glob("*.json"), key=lambda x: x.stat().st_mtime):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("status") in ("distilled", "archived"):
                continue
            ts_str = data.get("started_at", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts > cutoff:
                        continue
                except ValueError:
                    pass
            results.append(data)
        return results

    def list_pending_sessions(self) -> List[Dict[str, Any]]:
        """Summary list for the dreaming cron job — all non-distilled sessions."""
        sessions = self.get_pending_sessions(min_age_hours=0)
        return [
            {
                "session_id": s["session_id"],
                "started_at": s.get("started_at", ""),
                "platform": s.get("platform", ""),
                "topics": s.get("topics", []),
                "turn_count": s.get("turn_count", 0),
                "summary": s.get("summary", "")[:600],
            }
            for s in sessions
        ]

    def mark_session_distilled(self, session_id: str) -> bool:
        data = self.read_session(session_id)
        if not data:
            return False
        data["status"] = "distilled"
        self.write_session(session_id, data)
        return True
