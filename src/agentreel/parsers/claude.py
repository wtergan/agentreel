"""Parser for Claude Code session logs (``~/.claude/projects/<project>/<session>.jsonl``).

Each line is a JSON record. Only ``user`` / ``assistant`` / ``system`` records
carry conversation content; bookkeeping records (attachments, file-history,
titles, queue operations) are skipped except ``custom-title`` / ``summary``,
which name the session.

Quirks handled here:
* One assistant API message is written as several lines (one per content block)
  that share ``message.id`` and repeat the same ``usage``; usage is counted once
  per id.
* Tool results arrive as ``user`` records whose content is a list of
  ``tool_result`` blocks; they are not user turns.
* Harness-injected user records are flagged ``isMeta`` and dropped, as are
  sidechain (subagent) records unless ``include_sidechains`` is set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..model import Event, Session, Usage, parse_ts

# Tools whose input names a file the agent modified.
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def looks_like_claude(first_records: list[dict[str, Any]]) -> bool:
    return any("sessionId" in r or r.get("type") in {"summary", "file-history-snapshot"} for r in first_records)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return "" if content is None else json.dumps(content)


def parse_claude(records: Iterable[dict[str, Any]], path: Path, include_sidechains: bool = False) -> Session:
    session = Session(source="claude-code", session_id=path.stem)
    seen_usage: set[str] = set()
    usage = Usage()
    models: list[str] = []

    for r in records:
        rtype = r.get("type")
        if rtype == "custom-title" and r.get("customTitle"):
            session.title = r["customTitle"]
            continue
        if rtype == "summary" and r.get("summary") and not session.title:
            session.title = r["summary"]
            continue
        if rtype not in {"user", "assistant", "system"}:
            continue
        if r.get("isSidechain") and not include_sidechains:
            continue
        if r.get("sessionId"):
            session.session_id = r["sessionId"]
        if r.get("cwd") and not session.cwd:
            session.cwd = r["cwd"]
        ts = parse_ts(r.get("timestamp"))

        if rtype == "system":
            text = r.get("content") or (r.get("error") or {}).get("message") or r.get("subtype", "")
            if text:
                session.events.append(Event("system", str(text), ts, is_error=r.get("level") == "error"))
            continue

        msg = r.get("message") or {}
        content = msg.get("content")

        if rtype == "user":
            if r.get("isMeta"):
                continue
            if isinstance(content, str):
                session.events.append(Event("user", content, ts))
                continue
            for block in content or []:
                btype = block.get("type")
                if btype == "tool_result":
                    session.events.append(Event(
                        "tool_result", _result_text(block.get("content")), ts,
                        call_id=block.get("tool_use_id"), is_error=bool(block.get("is_error")),
                    ))
                elif btype == "text":
                    session.events.append(Event("user", block.get("text", ""), ts))
            continue

        # assistant
        mid = msg.get("id")
        model = msg.get("model")
        if model and model not in models and not model.startswith("<"):
            models.append(model)
        u = msg.get("usage")
        if u and mid and mid not in seen_usage:
            seen_usage.add(mid)
            usage.input_tokens += u.get("input_tokens") or 0
            usage.output_tokens += u.get("output_tokens") or 0
            usage.cache_read_tokens += u.get("cache_read_input_tokens") or 0
            usage.cache_write_tokens += u.get("cache_creation_input_tokens") or 0
        for block in content or []:
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                session.events.append(Event("assistant", block["text"], ts))
            elif btype == "thinking" and block.get("thinking", "").strip():
                session.events.append(Event("thinking", block["thinking"], ts))
            elif btype == "tool_use":
                name = block.get("name", "tool")
                inp = block.get("input") or {}
                files = []
                if name in _EDIT_TOOLS:
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if fp:
                        files.append(fp)
                session.events.append(Event(
                    "tool_call", "", ts, tool=name, call_id=block.get("id"), tool_input=inp, files=files,
                ))

    session.usage = usage
    session.models = models
    if not session.title:
        first = next((e.text for e in session.events if e.kind == "user"), "")
        session.title = first.strip().splitlines()[0][:100] if first.strip() else session.session_id
    return session
