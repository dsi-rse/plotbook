# Implementation decisions (beyond the spec) and why

- **The thread claim happens in the UserPromptSubmit hook** (turn_start.py matches the raw `/plotbook:start T` prompt),
  not in the skill. That keeps it deterministic, gives it session_id, and exit 2 blocks the prompt when a live
  session holds the thread. guard.sh lets that one prompt through before `.plotbook/` exists.
- **Liveness**: a claim stores the pid of the nearest ancestor process whose name contains "claude". SessionEnd releases
  the claim; a resumed session (`--resume`) reclaims its thread via `state/sessions/<sid>.thread`. `--steal` overrides.
- **Per-turn context line** (`[plotbook] thread=T next=T0005 leaf=shared_0007 ...`). It survives compaction, and
  number/leaf allocation never relies on model memory.
- **CLI tools live in `bin/`** (on the Bash PATH): plotbook-note, plotbook-delta, plotbook-viewer, plotbook-repro.
  plotbook-note takes the thread explicitly because the session id isn't visible to Bash.
- **Delta ownership** is recorded in `state/deltas.jsonl` by plotbook-delta. The Stop hook stages
  uncommitted deltas owned by its thread. The guard denies edits to committed deltas or another thread's deltas.
- **Commit scope** = journal ∪ changed thread-prefixed files under plots/ and scripts/ ∪ owned deltas ∪ transcript.
  Mechanics: `git add -- :(literal)paths` then `git commit --only -- paths`, so the user's own staged changes are untouched.
  The flock is `$(git-common-dir)/plotbook.lock`, repo-wide because several analyses share one index.
- **Divider** = `through` field in published.jsonl (the max artifact number existing at publish time, the user's choice),
  not the max number referenced in the comment.
- **Published immutability** is keyed on (thread, n) of artifacts referenced in published.jsonl, so it also covers
  that number's script and suffixed outputs. Unreferenced numbers stay editable.
- **Addendum auto-generation** happens on draft open only if `state/addendum-T.meta.json` basis != current publish basis,
  so the user's edits are never clobbered. Publishing archives the draft and addendum to `state/archive/` and deletes the meta.
- **Viewer**: Starlette + uvicorn + watchfiles + cairosvg + markdown-it-py via uv inline metadata. Thumbnails are
  browser-rendered SVG. publish.py runs under the viewer's interpreter, so cairosvg is normally available.
- **Viewer lifetime** (user request): one viewer per analysis (all threads). SessionEnd (sync_transcript.py,
  before the slow sync) calls claim_thread.stop_viewer: SIGTERM the viewer.json pid only if it is this root's viewer
  and no *other* claim has a live claude_pid. Every claimed prompt calls ensure_viewer (cheap /health probe), which
  restarts it after resume/crash, preferring the old port. Also fires on /clear (reason "clear"); /plotbook:start revives it.
  A running publish survives: the viewer launches it with Popen(start_new_session) + a daemon reader thread, and
  publish.emit() swallows BrokenPipe.
- **Plot-script header chdir** (replaces the earlier cwd-guard deviation): plot scripts carry
  `os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` before the exec line, so they run from any
  directory (`python <dir>/scripts/T0005.py`) with analysis-relative paths. Chosen over telling the model to
  `cd dir && ...`, because compound cd commands can trigger permission prompts. Old scripts with the guard still work
  from the analysis root; shared_0000 keeps its guard.
- **Repro** symlinks the analysis dir's untracked top-level entries (data files) into the worktree. It compares outputs
  ignoring `<metadata>`; `svg.hashsalt` is fixed in shared_0000 for deterministic ids.
- **Commit policy** (the user's request; overrides spec Decision 3's "every turn commits" and the §9 step 8 meta-commit):
  only turns that change files commit. Conversation-only turns, `!` commands, and the SessionEnd/PreCompact
  backstops just sync the transcript, and the next commit carries it along with all prompts since the last commit
  (the commit body). Publish makes exactly ONE commit: thread_pending (artifacts, owned deltas, transcripts, earlier
  manifest entries) plus the PNG previews; the comment pins that commit. The new published.jsonl entry can't be in
  the pinned commit (the comment URL doesn't exist yet), so it stays uncommitted and rides in the thread's next commit
  (pbcore.manifest_changes). The divider reads the working-tree file, so it moves immediately. Goal: one turn plus
  publish = two commits.
- Subject fallback when no prompt was recorded (`!` commands skip UserPromptSubmit): the newest user text in the
  filtered transcript.
- **PR creation from the publish dialog** (user request; plotbook previously refused to create PRs). publish.py
  find_pr() -> None makes pr_setup() check the creatable cases first: the push remote's *raw* URL
  (`git config remote.R.url`, not get-url, so tests can map a github.com URL via insteadOf) must be GitHub; gh must
  read the default branch; the branch must not be the default branch. Otherwise it emits {"need_pr": setup} and
  exits 3 before any commit or push. The viewer shows a title/description/draft form (title prefilled from the
  branch name, draft UNchecked by default: free-plan private repos reject draft PRs, user's call) and reruns with --create-pr <json>. create_pr pushes `-u` (sets the upstream),
  then `gh pr create --repo --base --head --title --body [--draft]` (--body is always passed: gh prompts otherwise).
  Not handled: no remote at all (error), forks (PR base = the push remote's repo).
- **Exec line uses compile() with the filename** (user request): `exec(compile(open(_f := "scripts/shared_NNNN.py").read(), _f, "exec"))`.
  Plain exec(open().read()) gave tracebacks `File "<string>", line 3` with no file or source line. `_f`, not `p`, so
  the walrus can't clobber a user variable. Per-analysis: pbcore.exec_style(root) = "legacy" when the analysis's
  immutable shared_0000.py lacks `_f := ` (scaffolded earlier, e.g. greenwave), because its provenance regex only knows
  the old line. Writes (guard, alloc_delta) require the analysis's style; reads (delta_tree, repro) accept both.
- **`/plotbook:start <thread> [<analysis-dir>] [--steal]`** (user request; README assumes Claude Code at the repo root).
  The dir is relative to the session cwd and created if missing. It must be inside the git repo and must not nest in
  another analysis. Without it: the analysis containing cwd, else this session's registered analysis, else the one
  analysis where the thread was claimed (resuming after /clear), else cwd.
  Session registry: `<git-common-dir>/plotbook/sessions/<sid>.json` = {root, thread}. It's written by claim(), kept at
  SessionEnd (resume needs it), and pruned after 90 days. pbcore.hook_root checks the registry first, then
  find_root(cwd). guard.sh also runs Python when that registry dir exists. The immutability guard takes the root from
  the *file's* path. CLI tools use pbcore.tool_root(thread): find_root(cwd), else a unique registry match.
  Context line carries `dir=<abs root>`. Moving a session to another analysis releases its old claim.
- find_root (and guard.sh) stop at a directory containing `.git`: a .plotbook/ above the repo is never an analysis.
