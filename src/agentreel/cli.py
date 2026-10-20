"""Command-line entry point.

    agentreel list                    # recent sessions from Claude Code and Codex
    agentreel render latest           # newest session -> <id>.reel.html
    agentreel render 3 --open         # 3rd row of `list`, open in browser
    agentreel render path/to/x.jsonl -o demo.html --redact 'acme-\\w+'
    agentreel stats latest

Session discovery looks in ``$CLAUDE_CONFIG_DIR`` (default ``~/.claude``)/projects
and ``$CODEX_HOME`` (default ``~/.codex``)/sessions. A session argument may be a
file path, ``latest``, a row number from ``list``, or a session-id prefix.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__
from .parsers import load_session
from .redact import Redactor
from .render import build_payload, render_html
from .stats import compute, fmt_duration, fmt_tokens


def session_roots() -> list[tuple[str, Path]]:
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"
    codex = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "sessions"
    return [("claude-code", claude), ("codex", codex)]


def discover(source: str | None = None) -> list[tuple[str, Path]]:
    """All session files, newest first by modification time."""
    found = []
    for name, root in session_roots():
        if source and name != source or not root.is_dir():
            continue
        found += [(name, p) for p in root.rglob("*.jsonl") if "subagents" not in p.parts]
    return sorted(found, key=lambda x: x[1].stat().st_mtime, reverse=True)


def resolve(arg: str, source: str | None = None) -> Path:
    p = Path(arg).expanduser()
    if p.is_file():
        return p
    sessions = discover(source)
    if not sessions:
        raise SystemExit("agentreel: no sessions found in ~/.claude/projects or ~/.codex/sessions")
    if arg == "latest":
        return sessions[0][1]
    if arg.isdigit():
        i = int(arg)
        if not 1 <= i <= len(sessions):
            raise SystemExit(f"agentreel: row {i} out of range (1-{len(sessions)})")
        return sessions[i - 1][1]
    matches = [s for _, s in sessions if arg in s.stem]
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(f"agentreel: {'ambiguous' if matches else 'no'} session matching {arg!r}")


def cmd_list(a: argparse.Namespace) -> int:
    rows = discover(a.source)[: a.limit]
    if not rows:
        print("no sessions found", file=sys.stderr)
        return 1
    for i, (src, path) in enumerate(rows, 1):
        try:
            s = load_session(path)
            title = s.title
        except ValueError as exc:
            title = f"(unreadable: {exc})"
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        tag = "claude" if src == "claude-code" else "codex "
        print(f"{i:>3}  {when}  {tag}  {title[:70]}")
    return 0


def _load(a: argparse.Namespace):
    path = resolve(a.session, getattr(a, "source", None))
    return path, load_session(path, include_sidechains=getattr(a, "sidechains", False))


def cmd_render(a: argparse.Namespace) -> int:
    path, session = _load(a)
    if a.hide_thinking:
        session.events = [e for e in session.events if e.kind != "thinking"]
    if a.title:
        session.title = a.title
    redactions: dict[str, int] = {}
    if not a.no_redact:
        r = Redactor(emails=a.redact_emails, extra_patterns=a.redact, literals=a.redact_literal)
        r.session(session)
        redactions = dict(r.counts)
    payload = build_payload(session, compute(session), max_output=a.max_output, redactions=redactions)
    out = Path(a.output or f"{session.session_id[:12]}.reel.html")
    if a.json:
        out = out.with_suffix(".json") if not a.output else out
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        out.write_text(render_html(payload), encoding="utf-8")
    kb = out.stat().st_size / 1024
    summary = ", ".join(f"{n} {k}" for k, n in sorted(redactions.items()) if n) or "nothing matched"
    print(f"wrote {out} ({kb:,.0f} KB) from {path.name}")
    print(f"redaction: {'disabled' if a.no_redact else summary}")
    if not a.no_redact:
        print("review before sharing: pattern redaction is best-effort, not a guarantee.")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_stats(a: argparse.Namespace) -> int:
    path, s = _load(a)
    st = compute(s)
    if a.json:
        print(json.dumps({"session": s.session_id, "source": s.source, "title": s.title,
                          "models": s.models, "usage": s.to_dict()["usage"], **st.to_dict()}, indent=2))
        return 0
    print(f"{s.title}\n{s.source} · {', '.join(s.models) or 'unknown model'} · {path}")
    print(f"active {fmt_duration(st.active_seconds)} (wall {fmt_duration(st.wall_seconds)}) · "
          f"{st.turns} turns · {st.tool_calls} tool calls · {st.tool_errors} errors · "
          f"{len(st.files)} files edited · {fmt_tokens(s.usage.total)} tokens")
    width = max((len(n) for n, _ in st.tools.most_common(10)), default=0)
    peak = max(st.tools.values(), default=1)
    for name, n in st.tools.most_common(10):
        print(f"  {name:<{width}}  {'█' * max(1, round(24 * n / peak))} {n}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentreel", description="Shareable HTML replays of Claude Code and Codex sessions.")
    p.add_argument("--version", action="version", version=f"agentreel {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="list recent sessions")
    ls.add_argument("-n", "--limit", type=int, default=20)
    ls.add_argument("--source", choices=["claude-code", "codex"])
    ls.set_defaults(fn=cmd_list)

    r = sub.add_parser("render", help="render a session to a self-contained HTML reel")
    r.add_argument("session", nargs="?", default="latest", help="path, 'latest', row number from list, or id prefix")
    r.add_argument("-o", "--output", help="output path (default: <session-id>.reel.html)")
    r.add_argument("--source", choices=["claude-code", "codex"], help="restrict session lookup")
    r.add_argument("--title", help="override the session title")
    r.add_argument("--open", action="store_true", help="open the reel in a browser")
    r.add_argument("--json", action="store_true", help="write the normalised payload as JSON instead")
    r.add_argument("--hide-thinking", action="store_true", help="drop reasoning blocks")
    r.add_argument("--sidechains", action="store_true", help="include Claude Code subagent records")
    r.add_argument("--max-output", type=int, default=4000, help="truncate each tool output to N chars (0 = keep all)")
    r.add_argument("--no-redact", action="store_true", help="disable secret and home-path redaction")
    r.add_argument("--redact-emails", action="store_true", help="also redact email addresses")
    r.add_argument("--redact", action="append", default=[], metavar="REGEX", help="extra pattern to redact (repeatable)")
    r.add_argument("--redact-literal", action="append", default=[], metavar="TEXT", help="extra literal to redact (repeatable)")
    r.set_defaults(fn=cmd_render)

    st = sub.add_parser("stats", help="print a session summary")
    st.add_argument("session", nargs="?", default="latest")
    st.add_argument("--source", choices=["claude-code", "codex"])
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except ValueError as exc:
        print(f"agentreel: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
