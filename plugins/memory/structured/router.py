"""Keyword-based topic router backed by INDEX.md."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List

_STOPWORDS = {
    "the", "a", "an", "is", "it", "in", "on", "at", "to", "for", "of",
    "and", "or", "with", "was", "are", "be", "this", "that", "we", "i",
    "you", "my", "our", "have", "has", "had", "not", "but", "can", "will",
    "how", "what", "when", "why", "do", "did", "done", "use", "used", "its",
    "also", "just", "now", "then", "get", "got", "let", "set", "run",
}

DEFAULT_INDEX = """\
# Structured Memory Index
Keyword routing: one section per memory file. Add entries as new skills/areas emerge.

## skills/navman
keywords: navman, navigation, maps, route, directions, gps, trip, waypoint, waze

## skills/haaretz
keywords: haaretz, puzzle, crossword, logic, bot_handler, puzzler, newspaper, sudoku

## skills/weather
keywords: weather, rain, forecast, open-meteo, weather2day, temperature, wind, humidity, ims

## skills/reolink
keywords: reolink, camera, cloud, renewal, token, cctv, livestream, 2fa

## infra/gateway
keywords: gateway, health, health_server, watchdog, restart, polling, systemd, hermes-gateway, active_sessions, telegram_polling, cron_jobs_active, runner

## infra/cron
keywords: cron, scheduler, tick, job, schedule, cronjob, recurring, dreaming, distillation, run_job

## infra/memory
keywords: memory, structured, plugin, dreaming, distillation, long-term, short-term, recall, memorymd, usermd, index

## infra/telegram
keywords: telegram, polling, bot, update, updater, connector, getme, webhook

## infra/whatsapp
keywords: whatsapp, bridge, baileys, wa, signal

## infra/hermes-agent
keywords: hermes, agent, run_agent, aiagent, session, platform, config, profile
"""


class TopicRouter:
    def __init__(self, index_path: Path):
        self.index_path = index_path
        self._cache: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        if not index_path.exists():
            index_path.write_text(DEFAULT_INDEX, encoding="utf-8")
        self._load()

    def _load(self) -> None:
        text = self.index_path.read_text(encoding="utf-8")
        self._cache = self._parse(text)

    def _parse(self, text: str) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        current: str | None = None
        for line in text.splitlines():
            m = re.match(r"^##\s+(\S+)", line)
            if m:
                current = m.group(1)
                result.setdefault(current, [])
                continue
            if current and line.startswith("keywords:"):
                kws = [k.strip() for k in line[len("keywords:"):].split(",") if k.strip()]
                result[current] = kws
        return result

    def reload(self) -> None:
        with self._lock:
            self._load()

    def detect_topics(self, text: str, top_n: int = 3) -> List[str]:
        """Score each memory file against tokens in text; return top matches."""
        tokens = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_\-]{2,}\b", text.lower())) - _STOPWORDS
        scores: Dict[str, int] = {}
        with self._lock:
            for target, keywords in self._cache.items():
                score = sum(
                    1 for kw in keywords
                    if kw in tokens or any(kw in tok for tok in tokens)
                )
                if score:
                    scores[target] = score
        return sorted(scores, key=lambda k: scores[k], reverse=True)[:top_n]

    def add_file(self, target: str, keywords: List[str]) -> None:
        with self._lock:
            self._cache[target] = keywords
            existing = self.index_path.read_text(encoding="utf-8") if self.index_path.exists() else DEFAULT_INDEX
            block = f"\n## {target}\nkeywords: {', '.join(keywords)}\n"
            self.index_path.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")

    def get_index_text(self) -> str:
        return self.index_path.read_text(encoding="utf-8") if self.index_path.exists() else DEFAULT_INDEX
