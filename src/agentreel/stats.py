"""Summary numbers for a session: tool mix, error rate, files touched, active time.

"Active time" sums gaps between consecutive events but caps each gap (default
5 minutes) so a session left open overnight is not reported as a 9-hour run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .model import Session


@dataclass
class Stats:
    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    tools: Counter = field(default_factory=Counter)
    files: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0
    active_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "tools": self.tools.most_common(),
            "files": self.files,
            "wall_seconds": round(self.wall_seconds),
            "active_seconds": round(self.active_seconds),
        }


def compute(session: Session, idle_cap_seconds: float = 300.0) -> Stats:
    st = Stats()
    seen_files: dict[str, None] = {}
    for e in session.events:
        if e.kind == "user":
            st.turns += 1
        elif e.kind == "tool_call":
            st.tool_calls += 1
            st.tools[e.tool or "tool"] += 1
            for f in e.files:
                seen_files.setdefault(f)
        elif e.kind == "tool_result" and e.is_error:
            st.tool_errors += 1
    st.files = list(seen_files)

    stamps = [e.ts for e in session.events if e.ts]
    if len(stamps) >= 2:
        st.wall_seconds = (stamps[-1] - stamps[0]).total_seconds()
        st.active_seconds = sum(
            min(max((b - a).total_seconds(), 0.0), idle_cap_seconds) for a, b in zip(stamps, stamps[1:])
        )
    return st


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_tokens(n: int) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)
