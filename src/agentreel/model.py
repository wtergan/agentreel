"""Tool-agnostic session model shared by every parser, the redactor and the renderer.

Parsers translate a vendor log (Claude Code, Codex, ...) into a :class:`Session`
holding an ordered list of :class:`Event`. Everything downstream only speaks this
model, so adding a new agent means writing one parser and nothing else.

Invariants:
* ``events`` are in log order (which is chronological for supported tools).
* A ``tool_result`` event carries the same ``call_id`` as the ``tool_call`` it
  answers when the source log provides one; the renderer pairs them by id.
* Timestamps are timezone-aware UTC ``datetime`` objects or ``None``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

EventKind = Literal["user", "assistant", "thinking", "tool_call", "tool_result", "system"]


@dataclass
class Event:
    kind: EventKind
    text: str = ""
    ts: datetime | None = None
    tool: str | None = None
    call_id: str | None = None
    tool_input: Any = None
    is_error: bool = False
    # Paths this event touched (edits/writes), used for the "files changed" panel.
    files: list[str] = field(default_factory=list)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class Session:
    source: str  # "claude-code" | "codex"
    session_id: str
    title: str = ""
    cwd: str = ""
    models: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    @property
    def started(self) -> datetime | None:
        return next((e.ts for e in self.events if e.ts), None)

    @property
    def ended(self) -> datetime | None:
        return next((e.ts for e in reversed(self.events) if e.ts), None)

    def to_dict(self) -> dict[str, Any]:
        def enc(o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: enc(v) for k, v in o.items()}
            if isinstance(o, list):
                return [enc(v) for v in o]
            return o

        d = enc(asdict(self))
        d["usage"]["total"] = self.usage.total
        return d


def parse_ts(value: Any) -> datetime | None:
    """Parse ISO-8601 strings (with trailing Z) or epoch seconds; None on absence."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        from datetime import timezone

        return datetime.fromtimestamp(value, tz=timezone.utc)
    s = str(value)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
