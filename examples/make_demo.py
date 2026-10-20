"""Generate examples/demo-session.jsonl: a synthetic Claude Code session used for the public demo reel.

Synthetic on purpose: real sessions contain private paths and prompts. The shape
mirrors real Claude Code logs closely enough to exercise every renderer feature
(thinking, shell, edits, a failing test, a fix, and a final summary).
"""

import json
from datetime import datetime, timedelta, timezone

SID = "7d3c1f0e-demo-4a51-9b2e-agentreel0001"
CWD = "/home/dev/src/tinyapi"
t = datetime(2026, 10, 12, 14, 3, 0, tzinfo=timezone.utc)
lines = [{"type": "custom-title", "customTitle": "Add per-client rate limiting to tinyapi", "sessionId": SID}]
n = 0


def stamp(sec):
    global t
    t += timedelta(seconds=sec)
    return t.isoformat().replace("+00:00", "Z")


def rec(kind, sec, message, **extra):
    lines.append({"type": kind, "sessionId": SID, "cwd": CWD, "isSidechain": False,
                  "timestamp": stamp(sec), "message": message, **extra})


def user(text, sec=0):
    rec("user", sec, {"role": "user", "content": text})


def asst(blocks, sec, out_tokens=200):
    global n
    n += 1
    rec("assistant", sec, {"id": f"msg_demo_{n}", "model": "claude-opus-5-5", "role": "assistant", "content": blocks,
                           "usage": {"input_tokens": 6, "output_tokens": out_tokens,
                                     "cache_read_input_tokens": 18000 + 900 * n, "cache_creation_input_tokens": 1200}})


def tool(name, inp, result, sec=3, err=False, think=None, say=None):
    global n
    blocks = []
    if think:
        blocks.append({"type": "thinking", "thinking": think})
    if say:
        blocks.append({"type": "text", "text": say})
    tid = f"toolu_demo_{n + 1}"
    blocks.append({"type": "tool_use", "id": tid, "name": name, "input": inp})
    asst(blocks, sec)
    rec("user", 1.5, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result,
                                                     "is_error": err}]})


user("tinyapi gets hammered by a couple of noisy clients. Add per-client rate limiting "
     "(60 req/min default, configurable), return 429 with Retry-After, and keep the tests green.")
tool("Bash", {"command": "git status --short && ls && sed -n '1,60p' tinyapi/app.py", "description": "Inspect repo and app entrypoint"},
     "app.py\ntinyapi/\ntests/\npyproject.toml\n\nfrom flask import Flask, jsonify, request\n\n"
     "def create_app(config=None):\n    app = Flask(__name__)\n    app.config.update(config or {})\n\n"
     "    @app.get('/health')\n    def health():\n        return jsonify(ok=True)\n\n"
     "    @app.get('/items')\n    def items():\n        return jsonify(items=[])\n\n    return app",
     sec=4, think="Start by reading the app factory and the test layout so the limiter plugs in without global state.",
     say="I'll look at how the app is assembled first.")
tool("Read", {"file_path": f"{CWD}/tests/test_app.py"},
     "import pytest\nfrom tinyapi.app import create_app\n\n@pytest.fixture\ndef client():\n"
     "    return create_app({'TESTING': True}).test_client()\n\ndef test_health(client):\n"
     "    assert client.get('/health').json == {'ok': True}\n", sec=6)
tool("Write", {"file_path": f"{CWD}/tinyapi/ratelimit.py", "content":
     '"""Sliding-window rate limiter keyed by client id."""\n\nimport time\nfrom collections import deque\n\n\n'
     "class RateLimiter:\n    def __init__(self, limit=60, window=60.0, clock=time.monotonic):\n"
     "        self.limit, self.window, self.clock = limit, window, clock\n        self.hits = {}\n\n"
     "    def check(self, key):\n        now = self.clock()\n        q = self.hits.setdefault(key, deque())\n"
     "        while q and now - q[0] >= self.window:\n            q.popleft()\n"
     "        if len(q) >= self.limit:\n            return False, self.window - (now - q[0])\n"
     "        q.append(now)\n        return True, 0.0\n"},
     "File created successfully", sec=22,
     think="A sliding window with a deque per client is simple and exact. Inject the clock so tests don't sleep.",
     say="Writing a small sliding-window limiter with an injectable clock so the tests stay fast.")
tool("Edit", {"file_path": f"{CWD}/tinyapi/app.py",
              "old_string": "    app.config.update(config or {})\n",
              "new_string": "    app.config.update(config or {})\n    limiter = RateLimiter(app.config.get('RATE_LIMIT', 60))\n\n"
                            "    @app.before_request\n    def throttle():\n"
                            "        ok, retry = limiter.check(request.remote_addr)\n        if not ok:\n"
                            "            resp = jsonify(error='rate limited')\n            resp.status_code = 429\n"
                            "            resp.headers['Retry-After'] = str(int(retry))\n            return resp\n"},
     "The file has been updated successfully.", sec=14)
tool("Write", {"file_path": f"{CWD}/tests/test_ratelimit.py", "content":
     "from tinyapi.app import create_app\n\n\ndef test_over_limit_returns_429_with_retry_after():\n"
     "    c = create_app({'TESTING': True, 'RATE_LIMIT': 2}).test_client()\n"
     "    assert [c.get('/items').status_code for _ in range(3)] == [200, 200, 429]\n"
     "    assert int(c.get('/items').headers['Retry-After']) > 0\n"},
     "File created successfully", sec=12)
tool("Bash", {"command": "pytest -q", "description": "Run the test suite"},
     "F.\n=================================== FAILURES ===================================\n"
     "__________________ test_over_limit_returns_429_with_retry_after ___________________\n"
     "E   NameError: name 'RateLimiter' is not defined\n\ntinyapi/app.py:6: NameError\n"
     "1 failed, 1 passed in 0.21s", sec=5, err=True)
tool("Edit", {"file_path": f"{CWD}/tinyapi/app.py",
              "old_string": "from flask import Flask, jsonify, request\n",
              "new_string": "from flask import Flask, jsonify, request\n\nfrom .ratelimit import RateLimiter\n"},
     "The file has been updated successfully.", sec=7, say="Forgot the import — fixing.")
tool("Bash", {"command": "pytest -q", "description": "Re-run the test suite"}, "..\n2 passed in 0.19s", sec=5)
tool("Bash", {"command": "git diff --stat", "description": "Summarise changes"},
     " tinyapi/app.py           | 12 ++++++++++++\n tinyapi/ratelimit.py     | 21 +++++++++++++++++++++\n"
     " tests/test_ratelimit.py  |  8 ++++++++\n 3 files changed, 41 insertions(+)", sec=4)
asst([{"type": "text", "text":
       "Rate limiting is in and the suite passes (**2 passed**).\n\n"
       "- `tinyapi/ratelimit.py` — sliding-window limiter, one deque per client, injectable clock\n"
       "- `app.py` — `before_request` hook returns **429** with `Retry-After`; limit comes from `RATE_LIMIT` (default 60/min)\n"
       "- `tests/test_ratelimit.py` — proves the 3rd request in a 2/min window is rejected\n\n"
       "One caveat: limits are per-process. Behind several workers you'd want a shared store such as Redis."}], 8, 320)
user("Nice. Make the key the X-API-Key header when present, IP otherwise.", 40)
tool("Edit", {"file_path": f"{CWD}/tinyapi/app.py",
              "old_string": "        ok, retry = limiter.check(request.remote_addr)\n",
              "new_string": "        key = request.headers.get('X-API-Key') or request.remote_addr\n        ok, retry = limiter.check(key)\n"},
     "The file has been updated successfully.", sec=9,
     think="Header first, fall back to the address. Add a test proving two keys get independent budgets.")
tool("Bash", {"command": "pytest -q", "description": "Run the test suite"}, "...\n3 passed in 0.20s", sec=11)
asst([{"type": "text", "text": "Done — clients sending `X-API-Key` now get their own budget; anonymous traffic is still keyed by IP. **3 passed**."}], 5, 90)

with open("demo-session.jsonl", "w") as f:
    for line in lines:
        f.write(json.dumps(line) + "\n")
print(f"wrote {len(lines)} records")
