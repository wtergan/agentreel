"""Parser for OpenAI Codex rollout logs (``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``).

Records are ``{"timestamp", "type", "payload"}``. Codex writes most content
twice: once as a model-facing ``response_item`` and once as a UI-facing
``event_msg``. To avoid duplicates this parser takes:

* user turns from ``event_msg/user_message``; newer Codex Desktop builds omit
  that event, so ``response_item`` user messages are used as a fallback with
  harness-injected context (AGENTS.md, ``<environment_context>`` tags) dropped,
* assistant text, reasoning summaries and tool calls from ``response_item``,
* token totals from the last ``event_msg/token_count`` (cumulative counter),
* the model from ``turn_context`` and the cwd from ``session_meta``.

Files touched are recovered from ``apply_patch`` headers
(``*** Update/Add/Delete File: <path>``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from ..model import Event, Session, Usage, parse_ts

_INJECTED = re.compile(r"^\s*(?:<[a-z_\-]+>|# AGENTS\.md instructions)")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)


def looks_like_codex(first_records: list[dict[str, Any]]) -> bool:
    return any(r.get("type") in {"session_meta", "response_item", "turn_context"} and "payload" in r for r in first_records)


def _parse_args(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _message_text(content: list[dict[str, Any]] | None) -> str:
    return "\n".join(c.get("text", "") for c in content or [] if c.get("type") in {"output_text", "input_text", "text"})


def parse_codex(records: Iterable[dict[str, Any]], path: Path) -> Session:
    session = Session(source="codex", session_id=path.stem.removeprefix("rollout-"))
    models: list[str] = []
    last_usage: dict[str, Any] | None = None
    fallback_users: list[Event] = []  # response_item user messages, used only if no event_msg ones exist
    saw_user_event = False

    for r in records:
        rtype = r.get("type")
        p = r.get("payload") or {}
        ptype = p.get("type")
        ts = parse_ts(r.get("timestamp"))

        if rtype == "session_meta":
            session.session_id = p.get("id") or session.session_id
            session.cwd = p.get("cwd") or session.cwd
        elif rtype == "turn_context":
            m = p.get("model")
            if m and m not in models:
                models.append(m)
        elif rtype == "event_msg":
            if ptype == "user_message" and (p.get("message") or "").strip():
                saw_user_event = True
                session.events.append(Event("user", p["message"], ts))
            elif ptype == "token_count" and p.get("info"):
                last_usage = p["info"].get("total_token_usage") or last_usage
            elif ptype == "thread_name_updated" and p.get("thread_name"):
                session.title = p["thread_name"]
            elif ptype in {"turn_aborted", "error", "context_compacted"}:
                session.events.append(Event("system", p.get("message") or ptype.replace("_", " "), ts,
                                            is_error=ptype == "error"))
        elif rtype == "response_item":
            if ptype == "message" and p.get("role") == "user":
                text = _message_text(p.get("content"))
                if text.strip() and not _INJECTED.match(text):
                    ev = Event("user", text, ts)
                    fallback_users.append(ev)
                    session.events.append(ev)
            elif ptype == "message" and p.get("role") == "assistant":
                text = _message_text(p.get("content"))
                if text.strip():
                    session.events.append(Event("assistant", text, ts))
            elif ptype == "reasoning":
                summary = "\n\n".join(s.get("text", "") for s in p.get("summary") or [])
                if summary.strip():
                    session.events.append(Event("thinking", summary, ts))
            elif ptype in {"function_call", "custom_tool_call"}:
                name = p.get("name", "tool")
                inp = _parse_args(p.get("arguments") if ptype == "function_call" else p.get("input"))
                files = _PATCH_FILE.findall(inp) if isinstance(inp, str) and name == "apply_patch" else []
                session.events.append(Event("tool_call", "", ts, tool=name, call_id=p.get("call_id"),
                                            tool_input=inp, files=[f.strip() for f in files]))
            elif ptype in {"function_call_output", "custom_tool_call_output"}:
                out = p.get("output")
                if isinstance(out, dict):
                    out = out.get("content") or json.dumps(out)
                text = out if isinstance(out, str) else json.dumps(out)
                err = bool(re.match(r"(?:Exit code|Process exited with code):? (?!0\b)\d+", text or ""))
                session.events.append(Event("tool_result", text, ts, call_id=p.get("call_id"), is_error=err))
            elif ptype == "web_search_call":
                action = p.get("action") or {}
                session.events.append(Event("tool_call", "", ts, tool="web_search",
                                            tool_input={"query": action.get("query", "")}))

    if saw_user_event:
        drop = {id(e) for e in fallback_users}
        session.events = [e for e in session.events if id(e) not in drop]
    if last_usage:
        cached = last_usage.get("cached_input_tokens") or 0
        session.usage = Usage(
            # Codex reports cached tokens as a subset of input tokens.
            input_tokens=(last_usage.get("input_tokens") or 0) - cached,
            output_tokens=last_usage.get("output_tokens") or 0,
            cache_read_tokens=cached,
        )
    session.models = models
    if not session.title:
        first = next((e.text for e in session.events if e.kind == "user"), "")
        session.title = first.strip().splitlines()[0][:100] if first.strip() else session.session_id
    return session
