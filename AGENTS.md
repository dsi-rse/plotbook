## Agent memory

`.agents/memory/` is persistent memory for coding agents working in this repo. It's written by agents, for agents; it isn't human documentation, so optimize entries for an agent reading them cold, not for polish. Use `.agents/memory/` *instead of* your built-in memory mechanism for this repo.

**At the start of every session**, read `.agents/memory/MEMORY.md`. It's an index: a one-line summary of each memory file with a link to it. Open individual files only when they're relevant to the current task.

**Save** things a future session would need but couldn't easily rediscover:
- Decisions and their reasons, especially rejected approaches (so they don't get revived)
- Non-obvious gotchas, failure modes, and how they were resolved
- Work in progress: what's done, what's next, open questions
- Preferences and corrections the user has given

**Don't save** anything derivable from the code, git history, or this file, and never save secrets or credentials.

**Maintenance:**
- One topic per file, with a descriptive filename (e.g. `auth-token-refresh.md`). Add each new file to `MEMORY.md`.
- Keep `MEMORY.md` short (under ~100 lines). It's a table of contents, not the memory itself.
- When something you find contradicts a memory, fix or delete the memory. Stale memory is worse than none.
- Update memory as you go, not only at the end of a session, and commit memory changes along with related code changes.
