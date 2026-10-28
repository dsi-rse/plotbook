# Claude Code behaviours verified by spike (CC 2.1.306, 2026-10-21)

Verified with a throwaway plugin run via `claude -p "/x:start primary" --plugin-dir ...`.

- UserPromptSubmit stdin has `prompt` = raw typed text for slash commands (`/plotbook:start primary`),
  plus session_id, transcript_path, cwd, prompt_id, permission_mode. Plain stdout (exit 0) reaches
  the model's context, and the skill body runs afterwards and can see it. That is why the thread
  claim lives in turn_start.py, not in the skill.
- Hook process: PWD = session cwd; CLAUDE_PLUGIN_ROOT and CLAUDE_PROJECT_DIR are set. Neither is set
  for model-run Bash commands, and no session-id env var exists there either.
- Plugin `bin/` is on the Bash tool's PATH (bare command names work).
- SKILL.md substitutes $ARGUMENTS, ${CLAUDE_SESSION_ID}, ${CLAUDE_PLUGIN_ROOT}.
- PostToolUse tool_input: Write{file_path,content}, Edit{file_path,old_string,new_string,replace_all},
  NotebookEdit{notebook_path,...}. There is NO MultiEdit tool in this version (matcher kept for safety).
- Stop: stdin has `last_assistant_message`. The final assistant text is NOT yet in the transcript
  JSONL when Stop fires; polling finds it after ~0.1 s. Exit 2 from Stop forces Claude to continue,
  so loud failures use exit 1.
- SessionEnd fires at the end of `claude -p` too (reason "other").
- Headless addendum: `claude -p --model sonnet --tools "" --no-session-persistence` works, with the
  prompt on stdin. Avoid `--bare` (it affects auth). Hooks still fire in -p, so the guard exits on
  PLOTBOOK_HEADLESS=1.
- CLI gotcha: `--allowedTools` is variadic and swallows a trailing prompt; put the prompt first.
- Transcript JSONL has ~25 top-level entry types (attachment, api-request*, atis-latch, ...).
  Attachments include the user's email and the system prompt; always drop them.
- 2026-10-28: a new top-level entry type `tools-used` ({"tools": [tool names], "sessionId"}) appeared, roughly once per
  turn. Added to ENTRY_TYPES as "drop". When fixing a TRANSCRIPT-SYNC-BROKEN, scan every ~/.claude/projects/*/*.jsonl
  for unknown types at once (that scan found only this one), not just the failing file.
