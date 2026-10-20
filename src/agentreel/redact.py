"""Scrub secrets and personal paths from a session before it is shared.

Redaction is on by default because the whole point of a reel is to be posted.
It is a best-effort pattern pass, not a guarantee: it catches common credential
shapes (provider API keys, GitHub/Slack/AWS tokens, JWTs, private-key blocks,
``KEY=value`` assignments to secret-looking names) and rewrites the user's home
directory to ``~``. Callers can add their own regexes and literal strings.

Every replacement is counted by rule name so the CLI can tell the user what was
removed. Redaction walks every string in the session, including tool inputs.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import Session

# (name, pattern, replacement). Order matters: specific token shapes run before
# the generic assignment rule so their label is the more informative one.
_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED private key]"),
    ("anthropic-key", re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED anthropic key]"),
    ("openai-key", re.compile(r"(?<![A-Za-z0-9\-])sk-(?:proj-|svcacct-)?(?=[A-Za-z_\-]*\d)[A-Za-z0-9_\-]{20,}"), "[REDACTED openai key]"),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
     "[REDACTED github token]"),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), "[REDACTED slack token]"),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED aws key]"),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[REDACTED google key]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "[REDACTED jwt]"),
    ("bearer", re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._\-~+/]{20,}=*"), r"\1[REDACTED]"),
    ("secret-assignment", re.compile(
        # Name must END in the keyword (so `tokenizer = ...` is left alone) and the
        # value must look like a credential: 12+ chars mixing letters and digits.
        r"(?i)\b([A-Z0-9_\-]*(?:API[_\-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD)S?)\b(\s*[=:]\s*)"
        r"(['\"]?)(?!\[REDACTED)(?=[^\s'\"]*\d)(?=[^\s'\"]*[A-Za-z])([A-Za-z0-9_\-./+=]{12,})\3(?![\w(.])"),
     r"\1\2\3[REDACTED]\3"),
]

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


@dataclass
class Redactor:
    home: str | None = field(default_factory=lambda: str(Path.home()))
    emails: bool = False
    extra_patterns: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self._extra = [re.compile(p) for p in self.extra_patterns]
        user = os.path.basename(self.home.rstrip("/")) if self.home else ""
        # Also catch the same user's Windows profile seen from WSL or natively.
        self._home_res = []
        if self.home and self.home not in {"/", "/root"}:
            self._home_res.append(re.compile(re.escape(self.home.rstrip("/")) + r"(?=/|\b)"))
        if user:
            self._home_res.append(re.compile(r"(?i)(?:/mnt/c|C:)[/\\]Users[/\\]" + re.escape(user) + r"(?=[/\\]|\b)"))

    def text(self, s: str) -> str:
        for name, pat, repl in _RULES:
            s, n = pat.subn(repl, s)
            if n:
                self.counts[name] += n
        for pat in self._extra:
            s, n = pat.subn("[REDACTED]", s)
            self.counts["custom"] += n
        for lit in self.literals:
            if lit and lit in s:
                self.counts["custom"] += s.count(lit)
                s = s.replace(lit, "[REDACTED]")
        if self.emails:
            s, n = _EMAIL.subn("[REDACTED email]", s)
            self.counts["email"] += n
        for pat in self._home_res:
            s, n = pat.subn("~", s)
            self.counts["home-path"] += n
        return s

    def value(self, v: Any) -> Any:
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, dict):
            return {k: self.value(x) for k, x in v.items()}
        return v

    def session(self, s: Session) -> Session:
        """Redact ``s`` in place and return it."""
        s.title = self.text(s.title)
        s.cwd = self.text(s.cwd)
        for e in s.events:
            e.text = self.text(e.text)
            e.tool_input = self.value(e.tool_input)
            e.files = [self.text(f) for f in e.files]
        return s
