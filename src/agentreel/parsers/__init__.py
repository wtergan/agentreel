"""Format detection and dispatch: ``load_session(path)`` returns a :class:`~agentreel.model.Session`."""

from __future__ import annotations

import json
from pathlib import Path

from ..model import Session
from .claude import looks_like_claude, parse_claude
from .codex import looks_like_codex, parse_codex


def _records(path: Path):
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Live sessions can end in a half-written line; anything else is real corruption.
                if fh.read().strip():
                    raise ValueError(f"{path}:{lineno}: invalid JSON line") from None
                return


def detect_format(path: Path) -> str:
    head = []
    for rec in _records(path):
        head.append(rec)
        if len(head) >= 20:
            break
    if looks_like_codex(head):
        return "codex"
    if looks_like_claude(head):
        return "claude-code"
    raise ValueError(f"{path}: not a recognised Claude Code or Codex session log")


def load_session(path: str | Path, include_sidechains: bool = False) -> Session:
    path = Path(path).expanduser()
    fmt = detect_format(path)
    if fmt == "codex":
        return parse_codex(_records(path), path)
    return parse_claude(_records(path), path, include_sidechains=include_sidechains)


__all__ = ["detect_format", "load_session"]
