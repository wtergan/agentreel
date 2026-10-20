import json
import re
from pathlib import Path

import pytest

from agentreel.cli import main
from agentreel.parsers import detect_format, load_session
from agentreel.redact import Redactor
from agentreel.render import build_payload, render_html
from agentreel.stats import compute

FX = Path(__file__).parent / "fixtures"
CLAUDE = FX / "claude_session.jsonl"
CODEX = FX / "codex_session.jsonl"


def kinds(s):
    return [e.kind for e in s.events]


def test_format_detection_should_identify_each_vendor():
    assert detect_format(CLAUDE) == "claude-code"
    assert detect_format(CODEX) == "codex"


def test_unknown_jsonl_should_raise(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"hello": 1}\n')
    with pytest.raises(ValueError):
        load_session(p)


def test_claude_parser_should_skip_meta_and_sidechains_and_dedupe_usage():
    s = load_session(CLAUDE)
    assert s.title == "Add due dates to todo CLI"
    assert kinds(s) == ["user", "thinking", "tool_call", "tool_result", "tool_call", "tool_result", "assistant"]
    assert s.models == ["claude-opus-5-5"]
    # m1 appears on two lines with identical usage; it must count once.
    assert (s.usage.input_tokens, s.usage.output_tokens, s.usage.cache_read_tokens) == (13, 60, 100)
    assert s.events[5].is_error


def test_claude_parser_should_include_sidechains_when_asked():
    assert "subagent chatter" in [e.text for e in load_session(CLAUDE, include_sidechains=True).events]


def test_codex_parser_should_prefer_event_user_messages_and_parse_patches():
    s = load_session(CODEX)
    assert [e.text for e in s.events if e.kind == "user"] == ["Fix the failing health check"]
    assert kinds(s) == ["user", "thinking", "tool_call", "tool_result", "tool_call", "tool_result", "assistant"]
    assert s.events[3].is_error  # non-zero exit
    assert not s.events[5].is_error
    assert s.events[4].files == ["app/routes.py"]
    assert (s.usage.input_tokens, s.usage.cache_read_tokens, s.usage.output_tokens) == (400, 600, 50)


def test_codex_parser_should_fall_back_to_response_items_without_event_messages(tmp_path):
    lines = [l for l in CODEX.read_text().splitlines() if '"user_message"' not in l]
    p = tmp_path / "rollout-new.jsonl"
    p.write_text("\n".join(lines) + "\n")
    users = [e.text for e in load_session(p).events if e.kind == "user"]
    assert users == ["Fix the failing health check"]  # environment_context dropped


def test_truncated_final_line_should_be_tolerated(tmp_path):
    p = tmp_path / "live.jsonl"
    p.write_text(CLAUDE.read_text() + '{"type": "assistant", "mess')
    assert load_session(p).title == "Add due dates to todo CLI"


def test_stats_should_count_tools_errors_and_files():
    st = compute(load_session(CLAUDE))
    assert st.tool_calls == 2 and st.tool_errors == 1
    assert st.files == ["/home/dev/projects/todo/cli.py"]
    assert st.wall_seconds == 20


@pytest.mark.parametrize("secret,label", [
    ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123", "anthropic-key"),
    ("sk-proj-Abc123def456ghi789jkl012mno", "openai-key"),
    ("ghp_" + "a1B2" * 9, "github-token"),
    ("AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
    ("xoxb-123456789012-abcdefghij", "slack-token"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.SflKxwRJSMeKKF2QT4fwpM", "jwt"),
    ("STRIPE_SECRET=sk_live_51HabcdEFGH1234", "secret-assignment"),
    ("export OPENAI_API_KEY='zz9AbCdEfGh1234567'", "secret-assignment"),
])
def test_redactor_should_remove_known_secret_shapes(secret, label):
    r = Redactor(home=None)
    out = r.text(f"before {secret} after")
    assert "REDACTED" in out and r.counts[label] >= 1
    assert out.startswith("before ") and out.endswith(" after")


@pytest.mark.parametrize("benign", [
    "tokenizer.pad_token = tokenizer.eos_token",
    "Tokenization: Implement a Tokenizer",
    "see chap-6-2-disk-method-washer-method-hw",
    "max_tokens = 4096",
    "the task-runner-for-everything works",
])
def test_redactor_should_leave_ordinary_code_alone(benign):
    r = Redactor(home=None)
    assert r.text(benign) == benign
    assert not r.counts


def test_redactor_should_rewrite_home_and_windows_profile_paths():
    r = Redactor(home="/home/dev")
    assert r.text("/home/dev/x and /mnt/c/Users/dev/y and C:\\Users\\dev\\z") == "~/x and ~/y and ~\\z"
    assert r.text("/home/developer/x") == "/home/developer/x"  # not a prefix match on other users


def test_redaction_should_cover_tool_inputs():
    s = Redactor(home="/home/dev").session(load_session(CLAUDE))
    assert s.events[2].tool_input["command"].endswith("~/projects/todo/cli.py")
    assert "sk-ant" not in s.events[0].text


def test_rendered_html_should_not_allow_script_breakout():
    html = render_html(build_payload(load_session(CLAUDE)))
    data = re.search(r'<script id="reel-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "</script>" not in data
    assert json.loads(data.replace("<\\/", "</"))["title"] == "Add due dates to todo CLI"


def test_payload_should_pair_results_with_calls_and_truncate_output():
    p = build_payload(load_session(CLAUDE), max_output=5)
    calls = [e for e in p["events"] if e["k"] == "tool_call"]
    assert len(calls) == 2 and not any(e["k"] == "tool_result" for e in p["events"])
    assert calls[1]["err"] and calls[1]["dk"] == "diff"
    assert "omitted" in calls[0]["out"] and p["omitted_chars"] > 0


def test_cli_render_should_write_redacted_reel(tmp_path, capsys):
    out = tmp_path / "r.html"
    assert main(["render", str(CLAUDE), "-o", str(out)]) == 0
    html = out.read_text()
    assert "sk-ant-api03" not in html and "agentreel" in html
    assert "1 anthropic-key" in capsys.readouterr().out


def test_cli_stats_json_should_report_summary(capsys):
    assert main(["stats", str(CODEX), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["source"] == "codex" and d["tool_calls"] == 2 and d["models"] == ["gpt-5.5"]
