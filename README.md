# plotbook

A Claude Code plugin that turns a GitHub Pull Request (PR) comment thread into a scientific lab notebook for exploratory data analysis.

This plugin functionally replaces Jupyter/marimo notebooks for data exploration in a Claude-centric way. Instead of interleaving code and plots, you ask for plots in a Claude Code terminal (or the desktop app) and write the high-level narrative in a GitHub PR. The Claude Code view is full of low-level instructions like "change the colors…" and "overlay the following…" but the GitHub PR view presents your discoveries, turning points, and conclusions. All the details of your analysis flow are git-tracked for reproducibility, but you can organize your thoughts on the comment thread as a human-written/human-readable story.

## Overview

Here's how it works:
1. You create an empty directory and a git branch in your GitHub repo.
2. You start Claude Code in that directory.
3. Use `/plotbook:start A` to start a thread named `A`. The plot viewer opens in a web browser window.
4. You ask for plots and other output to analyze your data.
5. Those plots and the scripts that made them are named sequentially (`A0001`, `A0002`, etc.) and are browsable in the viewer.
6. When you reach some sort of conclusion, you draft a comment in the viewer, clicking on plots to add whichever ones are relevant to the conclusion. Pending commits are git-pushed to GitHub with the comment.
7. GOTO 4.
8. Open another Claude Code in another terminal and `/plotbook:start B` to ask for more plots in parallel (`B0001`, `B0002`, etc.). Its web viewer opens in another browser tab.

In the end, the PR comment thread shows your human-written comments and chosen plots directly, but Claude's detailed output is hidden inside of `🤖 Extra details 🤖` blocks. Additionally, the code used to make the plots is checked in for reproducibility or forensics, as well as the full text of your Claude conversation. (If your repo is public, be careful what you say!)

In the Claude Code conversation, you can refer to any plot by its prefix, such as `A0003`. This is useful when you want to ask for "a plot like A0003, but with the following changes..."

The tool will recover if you manually push or add your own commits or comments. It doesn't try to be the only tool you use.

## How to install

```sh
claude plugin marketplace add dsi-rse/plotbook
claude plugin install plotbook@plotbook
```

**Dependencies:** Python with Matplotlib and anything else needed to make your plots, [git](https://git-scm.com/install/), [gh](https://cli.github.com/), and [uv](https://docs.astral.sh/uv/).

You don't have to give Claude git/GitHub permissions to use this tool. Even if you have

```yaml
"deny": ["Bash(git *)", "Bash(gh *)"]
```

in your `.claude/settings.json` (as I do), this plugin will work because the git/GitHub interactions are in the plugin's code, not invoked directly by Claude.

## Screenshots

Browsing recent plot images in one thread (`A`):

![](img/screenshot-browse.png)

Drafting a GitHub comment (click to add plots and edit or copy from the `🤖 Extra details 🤖` block):

![](img/screenshot-draft.png)

## File layout

Everything goes into the analysis directory that you created, with the following structure:

```
analysis-dir/
  plots/      outputs only: {thread}{NNNN}-{slug}.svg (and .geojson, …)
  scripts/    {thread}{NNNN}-{slug}.py plot scripts; shared_NNNN.py library deltas
  .plotbook/  transcripts/ (committed), published.jsonl, previews/, state/ (git-ignored)
```

Your Claude conversations are in `.plotbook/transcripts/`.

The primary format for the plots is SVG so that you can edit them (usually to remove unnecessary annotations or change labels). I recommend [Inkscape](https://inkscape.org/).

You can use code from other directories, but the plotting scripts in this directory are not intended for reuse. They're for reproducing the plots and remembering all the details of how they were made. Even the functions that are shared among multiple plots in this directory are versioned: `shared_NNNN.py` represents a tree of updates so that you can provisionally try a new technique and revert to build on an old version if you change your mind.

## More commands

* `/plotbook:start A` for some label like `A`, `B`, `C`: sets up the subdirectories and tells Claude to adhere to the filename convention.
* `/plotbook:push` starts a draft comment in the web viewer; equivalent to the "Draft comment" button in the web viewer.
* `/plotbook:repro A0003` reproduces a plot whose prefix is `A0003`.

Only `/plotbook:start` is required.

## Customizing

- To change the prompt that generates the `🤖 Extra details 🤖` text: copy `templates/addendum-prompt.md` to `~/.config/plotbook/addendum-prompt.md` and edit it. `PLOTBOOK_ADDENDUM_MODEL` selects the model (default `sonnet`).
- To run the viewer outside of Claude: `uv run --script <plugin>/server/viewer.py --root <analysis-dir>`.

## When something breaks

- If `TRANSCRIPT-SYNC-BROKEN` appears in the analysis root: Claude Code changed its internal transcript format. Your files are still committed. Add the new entry type to the table at the top of `scripts/sync_transcript.py`. The next sync recovers the full transcript and removes the marker.
- If you see "thread … is already claimed": another live session owns it. If that session crashed, use `/plotbook:start <thread> --steal`.

## Development

If you want to use plotbook while developing it, clone this repo and

```sh
claude --plugin-dir /path/to/plotbook
```

to install it only for that Claude Code session. After editing plotbook's code, do

```
/reload-plugins
! pkill -f "server/viewer.py --root"
! plotbook-viewer A
```

in Claude, where `A` is the letter of your thread.

You can also

```sh
claude plugin validate /path/to/plotbook
```

and

```sh
python3 -m pytest tests/
```

before submitting your improvements to the plugin.

Contributions are welcome as issues or pull requests!
