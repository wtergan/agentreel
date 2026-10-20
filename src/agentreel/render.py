"""Build the reel payload and inject it into the single-file HTML template.

The output HTML has no external requests: CSS, JS and data are inline, so a reel
can be emailed, attached to a PR, or dropped on any static host.

Payload shaping happens here rather than in JS so the template stays simple:
* tool calls are paired with their results by ``call_id``;
* each call gets a one-line ``summary`` and a typed ``detail`` (shell / diff / text);
* long tool outputs are truncated to ``max_output`` characters (head + tail kept),
  which is what keeps reels of multi-hour sessions to a few hundred KB.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from . import __version__
from .model import Event, Session
from .stats import Stats, compute

_PLACEHOLDER = "/*__REEL_DATA__*/null"


def _truncate(s: str, limit: int) -> tuple[str, int]:
    if limit <= 0 or len(s) <= limit:
        return s, 0
    head = int(limit * 0.7)
    tail = limit - head
    cut = len(s) - limit
    return f"{s[:head]}\n\n… [{cut:,} characters omitted] …\n\n{s[-tail:]}", cut


def _first_str(d: dict) -> str:
    for v in d.values():
        if isinstance(v, str) and v.strip():
            return v
    return ""


def describe_call(e: Event) -> tuple[str, str, str]:
    """Return (summary, detail, detail_kind) for a tool call. detail_kind: shell|diff|text."""
    name = e.tool or "tool"
    inp = e.tool_input
    if isinstance(inp, str):  # Codex custom tools (apply_patch) send raw text
        if name == "apply_patch":
            files = ", ".join(e.files) or "patch"
            return files, inp, "diff"
        return inp.splitlines()[0][:160] if inp else "", inp, "text"
    if not isinstance(inp, dict):
        return "", json.dumps(inp, indent=2), "text"

    # Shell-like tools across vendors.
    cmd = inp.get("command") if name in {"Bash", "shell", "local_shell"} else inp.get("cmd")
    if isinstance(cmd, list):
        cmd = cmd[-1] if len(cmd) >= 3 and cmd[0].endswith(("bash", "sh", "zsh")) else " ".join(cmd)
    if isinstance(cmd, str) and cmd:
        return (inp.get("description") or cmd.splitlines()[0])[:160], cmd, "shell"

    if name in {"Edit", "MultiEdit"}:
        edits = inp.get("edits") or [inp]
        lines = []
        for ed in edits:
            lines += ["-" + l for l in str(ed.get("old_string", "")).splitlines()]
            lines += ["+" + l for l in str(ed.get("new_string", "")).splitlines()]
            lines.append("")
        return inp.get("file_path", ""), "\n".join(lines).rstrip(), "diff"
    if name == "Write":
        body = "\n".join("+" + l for l in str(inp.get("content", "")).splitlines())
        return inp.get("file_path", ""), body, "diff"
    for key in ("file_path", "path", "pattern", "query", "url", "description", "prompt", "skill"):
        if isinstance(inp.get(key), str) and inp[key]:
            summary = inp[key]
            break
    else:
        summary = _first_str(inp)
    return summary.splitlines()[0][:160] if summary else "", json.dumps(inp, indent=2, ensure_ascii=False), "text"


def build_payload(session: Session, stats: Stats | None = None, max_output: int = 4000,
                  redactions: dict[str, int] | None = None) -> dict[str, Any]:
    stats = stats or compute(session)
    results = {e.call_id: e for e in session.events if e.kind == "tool_result" and e.call_id}
    calls = {e.call_id for e in session.events if e.kind == "tool_call" and e.call_id}
    t0 = session.started
    items: list[dict[str, Any]] = []
    omitted = 0
    for e in session.events:
        if e.kind == "tool_result" and e.call_id in calls:
            continue  # rendered inside its call
        item: dict[str, Any] = {"k": e.kind, "t": (e.ts - t0).total_seconds() if e.ts and t0 else None}
        if e.kind == "tool_call":
            summary, detail, kind = describe_call(e)
            detail, cut = _truncate(detail, max_output)
            omitted += cut
            item.update(tool=e.tool, summary=summary, detail=detail, dk=kind, files=e.files)
            res = results.get(e.call_id)
            if res is not None:
                out, cut = _truncate(res.text, max_output)
                omitted += cut
                item.update(out=out, err=res.is_error)
        else:
            text, cut = _truncate(e.text, max_output * 4 if e.kind in {"user", "assistant"} else max_output)
            omitted += cut
            item.update(x=text, err=e.is_error)
        items.append(item)

    return {
        "v": __version__,
        "source": session.source,
        "id": session.session_id,
        "title": session.title,
        "cwd": session.cwd,
        "models": session.models,
        "started": t0.isoformat() if t0 else None,
        "usage": session.to_dict()["usage"],
        "stats": stats.to_dict(),
        "redactions": dict(redactions or {}),
        "omitted_chars": omitted,
        "events": items,
    }


def render_html(payload: dict[str, Any]) -> str:
    template = resources.files("agentreel").joinpath("templates/reel.html").read_text(encoding="utf-8")
    if _PLACEHOLDER not in template:
        raise RuntimeError("reel template is missing its data placeholder")
    # Prevent "</script>" inside session text from terminating the script element.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = payload["title"].replace("&", "&amp;").replace("<", "&lt;")
    return template.replace(_PLACEHOLDER, data).replace("__REEL_TITLE__", title)
