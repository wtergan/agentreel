# agentreel: plan

## Current state (v0.1.0, 2026-10-19)
- Parsers for Claude Code and Codex; verified against 383 real local logs with zero parse errors (not committed)
- Redaction with tests for true positives and known false positives (`tokenizer = …`, `disk-method-…` slugs)
- Single-file HTML reel: replay, timeline strip, search/filters, light/dark, mobile; checked in headless Chromium with no console errors
- `docs/index.html` is the synthetic demo published via GitHub Pages

## Next
1. `agentreel serve`: local gallery of all sessions with cross-session stats (tool mix over time, error hot spots)
2. Parsers for Gemini CLI, Cursor, and Aider logs
3. `--from/--to` to clip a reel to a span of events, for sharing the interesting 2 minutes
4. Animated GIF/MP4 export of the replay for social posts
5. PyPI release
