# Gotchas found while building plotbook

- The viewer's PAGE_HEAD contains CSS `%` signs: use `head(title)` (str.replace), never `%`-formatting.
- GitHub `POST /markdown` wraps mermaid differently with and without `context`: `section[data-type=mermaid]`
  (with context) or `div.highlight-source-mermaid` (without). Math arrives as `<math-renderer>$..$</math-renderer>`;
  MathJax with `$`/`$$` delimiters typesets it. `<img src="plots/...">` srcs are preserved as-is.
- Re-running a script rewrites its SVG with new provenance (time and commit), so the file shows as modified.
  That's expected. Different matplotlib versions (e.g. Debian /usr/bin/python3 vs conda) shift layout.
- Tests run inside Claude Code, so claude_pid() resolves to the real Claude process: every simulated session
  looks "alive", which the duplicate-claim test relies on.
- The transcript filter fails on unknown entry types by design. Claude Code adds new metadata types often
  (e.g. `atis-latch`, `pr-link`). The fix is one line in ENTRY_TYPES; see claude-code-facts.md.
