"""Structured memory provider: per-skill/infra long-term files + short-term session capture."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "structured_memory",
    "description": (
        "Read or write structured long-term memory organized per skill or infra area.\n\n"
        "Actions:\n"
        "  add              — persist a typed entry (requires: target, type, content, why, apply)\n"
        "  read             — load a memory file (requires: target, e.g. 'skills/navman')\n"
        "  list             — show all files and the routing index\n"
        "  create_file      — create a new file for a new skill/area (requires: target; "
                             "provide description and keywords for routing)\n"
        "  list_pending_sessions — list short-term sessions awaiting distillation\n"
        "  read_session     — read a full short-term session (requires: session_id)\n"
        "  mark_distilled   — archive a processed session (requires: session_id)\n\n"
        "Write to long-term for: architectural decisions, bug root causes and fixes, "
        "user preferences per skill, key behavioral constraints discovered.\n"
        "Always fill 'why' (what caused this learning) and 'apply' (when/how to use it)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "read", "list", "create_file",
                         "list_pending_sessions", "read_session", "mark_distilled"],
            },
            "target": {
                "type": "string",
                "description": "Memory file, e.g. 'skills/navman' or 'infra/gateway'.",
            },
            "type": {
                "type": "string",
                "enum": ["feedback", "project", "reference", "user"],
                "description": "Entry type. Required for 'add'.",
            },
            "content": {"type": "string", "description": "The fact to remember."},
            "why": {"type": "string", "description": "Why this matters / root cause."},
            "apply": {"type": "string", "description": "When and how to use this in future."},
            "description": {"type": "string", "description": "Short description. For 'create_file'."},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Routing keywords. For 'create_file'.",
            },
            "session_id": {"type": "string", "description": "For 'read_session' / 'mark_distilled'."},
        },
        "required": ["action"],
    },
}

# Contexts where we do NOT write short-term session data (cron jobs other than dreaming)
_SKIP_SESSION_CONTEXTS = {"flush", "subagent"}


class StructuredMemoryProvider(MemoryProvider):
    """Local structured memory: per-skill/infra long-term files + short-term session capture."""

    @property
    def name(self) -> str:
        return "structured"

    def is_available(self) -> bool:
        return True  # local only, no credentials needed

    def initialize(self, session_id: str, **kwargs) -> None:
        from .store import StructuredStore
        from .router import TopicRouter

        hermes_home = Path(kwargs.get("hermes_home", Path.home() / ".hermes"))
        base = hermes_home / "memories" / "structured"
        self._store = StructuredStore(base)
        self._router = TopicRouter(base / "INDEX.md")
        self._agent_context = kwargs.get("agent_context", "primary")
        self._platform = kwargs.get("platform", "unknown")
        self._capture_session = self._agent_context not in _SKIP_SESSION_CONTEXTS

        ts = datetime.now(timezone.utc)
        slug = re.sub(r"[^a-z0-9]", "_", self._platform.lower())[:20]
        self._session_id = f"{ts.strftime('%Y-%m-%dT%H-%M-%S')}_{slug}"
        self._session_started = ts.isoformat()
        self._session_topics: List[str] = []
        self._session_notes: List[str] = []

    # ── System prompt ────────────────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        files = self._store.list_long_term_files()
        file_lines = "\n".join(
            f"  {cat}/{name}"
            for cat, names in sorted(files.items())
            for name in sorted(names)
        ) or "  (none yet — use structured_memory create_file to add one)"
        return (
            "## Structured Long-Term Memory\n"
            f"Available memory files:\n{file_lines}\n\n"
            "Use `structured_memory` to read context or write learnings. "
            "Write when you discover: architectural decisions, bug root causes, "
            "user preferences for a skill, or key behavioral constraints. "
            "Every entry needs: target, type, content, why, apply."
        )

    # ── Prefetch ─────────────────────────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        topics = self._router.detect_topics(query)
        if not topics:
            return ""
        parts: List[str] = []
        for target in topics[:2]:
            content = self._store.read_long_term(target)
            if content and content.strip():
                # Tail to stay lightweight in the context window
                excerpt = content[-1800:] if len(content) > 1800 else content
                parts.append(f"[Memory: {target}]\n{excerpt}")
        for s in self._store.get_recent_sessions(topics=topics, limit=1):
            summary = s.get("summary", "")
            if summary:
                date = s.get("started_at", "")[:10]
                parts.append(f"[Recent session {date}]\n{summary}")
        return "\n\n".join(parts)

    # ── Session capture hooks ─────────────────────────────────────────────────

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        if not self._capture_session:
            return
        for topic in self._router.detect_topics(message):
            if topic not in self._session_topics:
                self._session_topics.append(topic)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        self._session_notes.append(f"[builtin {action}→{target}]: {content[:250]}")

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        if self._capture_session and messages:
            self._session_notes.append(
                f"[compression: {len(messages)} messages condensed]"
            )
        return ""

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._capture_session:
            return
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs and not self._session_notes:
            return
        summary_parts: List[str] = []
        if user_msgs:
            summary_parts.append(f"First: {_text_of(user_msgs[0])[:200]}")
            if len(user_msgs) > 1:
                summary_parts.append(f"Last: {_text_of(user_msgs[-1])[:200]}")
        summary_parts.extend(self._session_notes[:20])
        data = {
            "session_id": self._session_id,
            "started_at": self._session_started,
            "platform": self._platform,
            "topics": self._session_topics,
            "status": "pending",
            "turn_count": len(user_msgs),
            "summary": "\n".join(summary_parts),
        }
        try:
            self._store.write_session(self._session_id, data)
        except Exception as e:
            logger.debug("Failed to write session file: %s", e)

    # ── Tool ─────────────────────────────────────────────────────────────────

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [_TOOL_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        action = args.get("action", "")
        try:
            if action == "add":
                return self._add(args)
            if action == "read":
                return self._read(args)
            if action == "list":
                return self._list()
            if action == "create_file":
                return self._create_file(args)
            if action == "list_pending_sessions":
                return json.dumps({"pending": self._store.list_pending_sessions()})
            if action == "read_session":
                sid = args.get("session_id", "")
                data = self._store.read_session(sid)
                return json.dumps(data or {"error": "session not found"})
            if action == "mark_distilled":
                sid = args.get("session_id", "")
                ok = self._store.mark_session_distilled(sid)
                return json.dumps({"ok": ok, "session_id": sid})
            return json.dumps({"error": f"Unknown action: {action}"})
        except Exception as e:
            logger.exception("structured_memory error")
            return json.dumps({"error": str(e)})

    def _add(self, args: Dict[str, Any]) -> str:
        for field in ("target", "type", "content", "why", "apply"):
            if not args.get(field):
                return json.dumps({"error": f"Missing required field: {field}"})
        self._store.write_long_term_entry(args["target"], {
            k: args[k] for k in ("type", "content", "why", "apply")
        })
        self._session_notes.append(f"[wrote {args['type']} → {args['target']}]: {args['content'][:150]}")
        return json.dumps({"ok": True, "target": args["target"], "type": args["type"]})

    def _read(self, args: Dict[str, Any]) -> str:
        target = args.get("target", "")
        if not target:
            return json.dumps({"error": "target required"})
        content = self._store.read_long_term(target)
        return json.dumps({"target": target, "content": content or "(empty)"})

    def _list(self) -> str:
        return json.dumps({
            "files": self._store.list_long_term_files(),
            "index": self._router.get_index_text(),
        })

    def _create_file(self, args: Dict[str, Any]) -> str:
        target = args.get("target", "")
        if not target:
            return json.dumps({"error": "target required"})
        result = self._store.create_long_term_file(target, args.get("description", ""))
        keywords = args.get("keywords", [])
        if keywords:
            self._router.add_file(target, keywords)
        return json.dumps({"ok": True, "result": result})


def _text_of(msg: Dict[str, Any]) -> str:
    c = msg.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""
