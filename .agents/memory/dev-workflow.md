# Dev workflow: the user dogfoods plotbook live from this working copy

The user runs plotbook in other Claude Code sessions via `claude --plugin-dir ~/dsi/plotbook`
(no cache copy), and asks this repo's session for fixes while using it. Consequences:
- Edits to scripts/*.py, guard.sh, bin/*, and templates take effect on the very next hook or command
  invocation, uncommitted or not. Never leave a half-edited file; run `python3 -m pytest tests -q`
  before reporting a fix as done.
- hooks/hooks.json, skills/*/SKILL.md, and plugin.json need `/reload-plugins` in the other session.
  A model that has already loaded the conventions skill must re-read it.
- The viewer is a long-running process. After editing server/viewer.py, it must be restarted:
  `pkill -f "server/viewer.py --root"`, then `plotbook-viewer <thread>`.
- templates/shared_0000.py is copied into each analysis at scaffold time, so changes reach only new analyses
  (by design: deltas are immutable).
