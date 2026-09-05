"""Call budget and response cache for the semantic analyzer.

A free API tier is a hard constraint, not a soft one: exceeding it fails the
run and, on some tiers, penalises the account. Three mechanisms keep spend
low, in order of how much they save:

1. **Cache** -- a response is keyed by the exact content that produced it, so
   re-running over an unchanged workflow costs zero calls.
2. **Batching** -- one request enriches many nodes (see `enrich.py`), so cost
   scales with workflow size, not node count.
3. **Ledger** -- a persistent per-day counter that refuses the call that would
   cross the limit, rather than discovering the limit from a 429.

State lives outside the repository, under `AIFLOW_HOME` (default
`~/.aiflow`), because it is per-machine and must never be committed.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["Ledger", "Cache", "BudgetExceeded", "home", "DEFAULT_DAILY_LIMIT"]

DEFAULT_DAILY_LIMIT = 50
RETENTION_DAYS = 30


class BudgetExceeded(RuntimeError):
    """Raised before a call is made, never after."""


def home() -> Path:
    return Path(os.environ.get("AIFLOW_HOME") or Path.home() / ".aiflow")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class Ledger:
    """Per-UTC-day call counter, persisted across runs."""
    path: Path
    limit: int

    @classmethod
    def open(cls, limit: int | None = None) -> "Ledger":
        if limit is None:
            raw = os.environ.get("AIFLOW_DAILY_LIMIT")
            limit = int(raw) if raw and raw.isdigit() else DEFAULT_DAILY_LIMIT
        return cls(path=home() / "usage.json", limit=limit)

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {k: int(v) for k, v in data.get("days", {}).items()
                if isinstance(v, int) or str(v).isdigit()}

    def _save(self, days: dict[str, int]) -> None:
        cutoff = time.time() - RETENTION_DAYS * 86400
        kept = {d: n for d, n in days.items()
                if datetime.strptime(d, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc).timestamp() >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"days": kept}, indent=2, sort_keys=True))
        tmp.replace(self.path)          # atomic, so a crash cannot corrupt the ledger

    def used_today(self) -> int:
        return self._load().get(_today(), 0)

    def remaining(self) -> int:
        return max(0, self.limit - self.used_today())

    def check(self, calls: int) -> None:
        """Raise if `calls` more requests would cross the daily limit."""
        if calls > self.remaining():
            raise BudgetExceeded(
                f"{calls} call(s) needed but only {self.remaining()} of "
                f"{self.limit} left today (used {self.used_today()}). "
                f"Raise it with AIFLOW_DAILY_LIMIT, or wait for the UTC day to roll over."
            )

    def record(self, calls: int = 1) -> None:
        days = self._load()
        days[_today()] = days.get(_today(), 0) + calls
        self._save(days)


@dataclass
class Cache:
    """Content-addressed response cache.

    The key covers everything that could change an answer -- model, prompt
    version, and the exact payload -- so a hit is always a valid substitute for
    the call, and a changed prompt never silently reuses old output.
    """
    path: Path
    enabled: bool = True

    @classmethod
    def open(cls, enabled: bool = True) -> "Cache":
        return cls(path=home() / "cache", enabled=enabled)

    @staticmethod
    def key(model: str, prompt_version: str, payload: object) -> str:
        blob = json.dumps([model, prompt_version, payload], sort_keys=True,
                          separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    def get(self, key: str):
        if not self.enabled:
            return None
        try:
            return json.loads((self.path / f"{key}.json").read_text())["response"]
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, response) -> None:
        if not self.enabled:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            (self.path / f"{key}.json").write_text(
                json.dumps({"key": key, "response": response,
                            "stored_at": datetime.now(timezone.utc).isoformat()}))
        except OSError:
            pass                        # a cache that cannot write is still correct

    def clear(self) -> int:
        if not self.path.is_dir():
            return 0
        files = list(self.path.glob("*.json"))
        for f in files:
            f.unlink(missing_ok=True)
        return len(files)
