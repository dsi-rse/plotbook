---
description: Conventions for working in a plotbook analysis directory (one with plots/, scripts/, .plotbook/). Use whenever making plots, writing analysis scripts, changing shared plotting helpers, or answering questions about how/why an earlier plot was made, in a session that has a "[plotbook] thread=..." context line.
allowed-tools: Bash(plotbook-note *), Bash(plotbook-delta *), Bash(plotbook-viewer *)
---
# plotbook conventions

The analysis directory is an exploratory-analysis lab notebook. Every prompt brings a context line,
`[plotbook] thread=T dir=/path/to/analysis next=T0005 leaf=shared_0007`, that says which thread this
session owns, the analysis directory, the next artifact name, and the shared-library leaf to pin.

## Layout and running
- Paths below (`plots/`, `scripts/`, `.plotbook/`) are inside the analysis directory, `dir=`. Claude Code
  may be running elsewhere (usually the repo root), so in file tools and commands write them as `<dir>/scripts/...`.
- `plots/`: outputs only. `scripts/`: plot scripts and shared deltas. `.plotbook/`: infrastructure; never edit it.
- Run scripts as `python <dir>/scripts/T0005-slug.py`, from wherever you are; don't `cd`. The header's
  `os.chdir` line makes every path inside a script relative to the analysis directory.
- Don't create anything else at the top of the analysis directory.

## Artifacts
- Name new work `T{n:04d}-{slug}`, using the `next=` number. The slug is short kebab-case, e.g. `primary0005-mass-by-island`.
  The script is `scripts/T0005-slug.py` and its output is `plots/T0005-slug.svg`.
- One script per artifact. **WET**: each script is a complete, standalone recipe. Scripts never
  import or exec other plot scripts; the only shared code is the exec chain (below).
- Save with the `save()` helper (from `shared_0000.py`): `save(fig)` → `plots/<script-stem>.svg`.
  Use `save(fig, suffix="zoom")` for extra outputs, and `output_path("geojson")` for other file types.
  Outputs are SVG with text kept as text (editable in Inkscape); don't override those rcParams.
- Before the user publishes an artifact in a PR comment, you may revise its script and re-run it in later turns.
  Once it's published it is immutable: corrections get a new number. Don't overwrite another thread's files.
  Threads are single-writer.
- How scripts load data is up to you and the user. plotbook imposes nothing.

## Script header
Every plot script starts like this. Imports go first. The `os.chdir` line comes next, so that the
script runs from any directory and its paths (the exec line's included) are relative to the analysis
directory. Then comes the library pin:
```python
"""primary0005: penguin mass by island"""
import os
import numpy as np
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the analysis directory
exec(compile(open(_f := "scripts/shared_0007.py").read(), _f, "exec"))
```

## Shared library: append-only delta tree
- Load the library with exactly one exec line in the canonical form above, at top level, pinning
  the `leaf=` from the context line. Copy the previous script's line unless deliberately branching.
  (Analyses started before this form use `exec(open("scripts/shared_0007.py").read())` instead; keep
  whichever form the analysis's scripts already use. The guard names the expected form if you get it wrong.)
- Each delta's first line execs its parent, so the leaf pulls in its ancestors root-first into the
  script's globals. Later definitions override earlier ones everywhere, because the helpers share those globals.
- **Never edit an existing delta.** To add or change helpers, run
  `plotbook-delta --thread T --parent 0007`. It creates `scripts/shared_NNNN.py` with the correct
  first line and prints its name. Then add only the new or changed definitions below that first line
  (Read the file, then Edit it). When an interface changes, re-emit the changed functions in the new delta;
  dependents pick them up through the shared namespace.
- Tangents: parent a new delta on any older node. To return to the assumptions from before the tangent,
  parent on the pre-tangent node. Old scripts stay reproducible because nothing they pin ever changes.
- `shared` is reserved; it is never a thread name.

## Git: hooks own it all
- **Never run `git` or `gh`.** Not even read-only. The Stop hook commits each file-changing turn's files
  and transcript automatically. Pushing and PR comments happen only when the user presses Publish
  in the viewer (`/plotbook:push` opens it).
- **At the end of every turn that created or changed files**, run
  `plotbook-note T "<one-line summary of what the user asked for>"`. Write it in the user's register,
  imperative, about 50 characters or fewer, e.g. `plotbook-note primary "move legend to upper right"`.
  It becomes the commit subject, after the `[T0005]` range. Run it before your final reply.
- Keep replies about what you made short. The user writes the PR narrative; detailed explanations
  are summarized separately for the "🤖 Extra details" block.

## Threads
- Each Claude Code session owns one thread, claimed by the user with `/plotbook:start <thread> [<analysis-dir>]`.
  If there's no `thread=` context line, tell the user to run it. Don't produce numbered artifacts until then.
- The viewer URL is in `<dir>/.plotbook/state/viewer.json`. Run `plotbook-viewer T` to (re)start the viewer and print its URL.

## Provenance questions
For questions like "what was I asking for when primary0042 was made?" or "why did we choose that cut?":
- `grep -n "primary0042" <dir>/.plotbook/transcripts/*.jsonl` finds the dialogue. Each line is JSON:
  `{"role","ts","text"}`, or `{"event":"wrote"|"ran",...}` anchors. Read the user turns around the anchor.
- `<dir>/.plotbook/published.jsonl` lists which artifacts were published in which PR comment (`comment_url`).
- SVGs carry JSON provenance in their `<dc:description>` metadata: script, leaf, commit, session, and time.
- You can't run git log yourself. If the user needs commit history, suggest they run
  `! git log --follow -- <dir>/plots/<file>`.
