# plotbook

A Claude Code plugin that turns a directory + git branch + GitHub PR into a scientific lab
notebook for exploratory data analysis.

- **You write the narrative** as PR comments; Claude's detailed account of each result goes into a
  collapsed `🤖 Extra details 🤖` block that you edit before publishing.
- **Every result is a plain file.** Numbered, self-contained plot scripts (`scripts/primary0042-….py`)
  write SVGs (`plots/primary0042-….svg`, text kept editable for Inkscape). There's no server to start
  and no kernel. Look at plots with any file manager; re-run any one with `python`.
- **Everything is git-bound.** Each conversational turn is committed automatically: the scripts,
  the outputs, and a filtered transcript of the conversation that produced them. Nothing is pushed or
  posted until you press Publish.

## Install

```sh
claude plugin marketplace add dsi-rse/plotbook   # or via the dsi-rse/skills marketplace
claude plugin install plotbook@plotbook
```

Requirements: `git`, `python3` (3.9+) with matplotlib for your scripts, and [`uv`](https://docs.astral.sh/uv/)
for the viewer. Publishing needs the `gh` CLI (logged in) and an SVG→PNG converter. One comes with the
viewer's environment (`cairosvg`); `rsvg-convert` or Inkscape also work.

The hooks do nothing outside analysis directories, so installing at user scope is safe.
plotbook never asks Claude to run `git` or `gh`; all git work happens in the plugin's own scripts.
It therefore works with `"deny": ["Bash(git *)", "Bash(gh *)"]` in your settings.

## Use

1. In your repo, create the analysis branch and open a (draft) PR for it. plotbook never creates branches or PRs.
2. `cd` into the analysis directory (any directory in the repo; a repo can hold several) and start Claude Code.
3. `/plotbook:start primary` claims the thread `primary` for this session. It creates `plots/`, `scripts/`,
   and `.plotbook/`, and prints the viewer URL. Keep that tab open. New plots appear on its stage as they're
   written; arrow keys walk the history.
4. Ask for plots. Each turn ends in a commit like `[primary0003–primary0004] split by island`.
5. For a side question, open another Claude Code session in the same directory and run `/plotbook:start tangent`.
   Threads are independent, numbered sequences, each with its own viewer tab.
6. `/plotbook:push` opens the draft view. Write your comment there, and click thumbnails to insert plots
   at the cursor. Edit the prefilled Extra-details pane (empty it to omit the block), then press **Publish**.
   This commits PNG previews, pushes, and posts the comment. Images are pinned to the pushed commit and
   link to their SVGs, so they keep rendering after a squash merge and branch deletion.
7. `/plotbook:repro primary0003` re-runs an old script from its commit in a temporary `git worktree`,
   without touching your working tree.

## Layout

```
analysis-dir/
  plots/      outputs only: {thread}{NNNN}-{slug}.svg (and .geojson, …)
  scripts/    {thread}{NNNN}-{slug}.py plot scripts; shared_NNNN.py library deltas
  .plotbook/  transcripts/ (committed), published.jsonl, previews/, state/ (git-ignored)
```

Run scripts from the analysis root: `python scripts/primary0003-foo.py`.

**Shared library.** Helpers live in an append-only tree of deltas. Each `shared_NNNN.py` starts with
`exec(open("scripts/shared_PPPP.py").read())`, naming its parent, and adds or overrides definitions.
A plot script pins one leaf with the same line. Deltas are never edited, so old scripts keep reproducing.
To go back to earlier assumptions, make a new delta whose parent is an older node.

## Customizing

- Extra-details prompt: copy `templates/addendum-prompt.md` to `~/.config/plotbook/addendum-prompt.md` and edit it.
  `PLOTBOOK_ADDENDUM_MODEL` selects the model (default `sonnet`).
- Viewer by hand: `uv run --script <plugin>/server/viewer.py --root <analysis-dir>`.

## When something breaks

- `TRANSCRIPT-SYNC-BROKEN` appears in the analysis root: Claude Code changed its internal transcript
  format. Your files are still committed. Add the new entry type to the table at the top of
  `scripts/sync_transcript.py`. The next sync recovers the full transcript and removes the marker.
- "thread … is already claimed": another live session owns it. If that session crashed, use
  `/plotbook:start <thread> --steal`.

## Development

```sh
claude --plugin-dir ./plotbook     # then /reload-plugins after edits
claude plugin validate ./plotbook
python3 -m pytest tests/
```
