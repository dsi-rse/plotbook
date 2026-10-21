# Agent memory index

Read this at session start. Open linked files only when relevant to the current task. See the "Agent memory" section of AGENTS.md for rules on what to save and how.

## Current state
- [status.md](status.md): what's built and verified, and what's pending (live GitHub checks).

## Decisions
- [DESIGN.md](DESIGN.md): motivations and rationale behind the spec (read when a choice is underdetermined).
- [implementation-decisions.md](implementation-decisions.md): choices made beyond the spec (claim-in-hook, divider `through`, plot-script cwd guard, ...).

## Gotchas
- [claude-code-facts.md](claude-code-facts.md): verified Claude Code hook/skill/transcript behaviours (Stop lag, exit-2 semantics, bin/ PATH).
- [gotchas.md](gotchas.md): viewer/GitHub-markdown/test pitfalls hit during the build.

## User preferences
- [dev-workflow.md](dev-workflow.md): the user runs plotbook live via --plugin-dir from this working copy; what needs reload/restart.
- The user chose: the divider goes after the newest *existing* artifact; live GitHub tests on jpivarski/penguin-science (private), with confirmation before pushing or commenting.
