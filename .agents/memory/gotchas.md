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
- Viewer layout: body is a 100dvh (100vh fallback) flex column with overflow hidden; the stage or draft grid
  is `flex:1; min-height:0`. Don't reintroduce `calc(100vh - Npx)` heights: they caused a page-level scrollbar
  that moved a few pixels. Strips have auto height, so a classic (non-overlay) horizontal scrollbar is added
  below the thumbnails instead of clipping their labels.
- Strip wheel handling: vertical-dominant wheel events are converted to horizontal scrolling (preventDefault,
  passive:false). Horizontal-dominant ones (trackpad, tilt wheel, Shift+wheel) are left native, and so is Ctrl+wheel (zoom).
- Italic PNG previews: uv built the viewer env on miniforge's Python, so cairosvg loaded miniforge's
  libcairo/libfontconfig. That fontconfig misreads the system's EB Garamond .woff files and resolved
  "DejaVu Sans" to EBGaramond08-Italic. The fix (publish.isolate_fonts) points FONTCONFIG_FILE at a generated
  fonts.conf containing only matplotlib's mpl-data/fonts/ttf, with a DejaVu Sans fallback for every family
  (otherwise unknown families hit matplotlib's box-drawing LastResort font). It applies to cairosvg, rsvg-convert,
  and Inkscape, and must be set before cairosvg is imported. Previews are always re-rendered so stale ones aren't reused.
  Debug with: FONTCONFIG_FILE=<conf> fc-match 'DejaVu Sans'.
- The template sets font.family="sans-serif" + font.sans-serif=["DejaVu Sans"], so SVG text is
  `font-family: 'DejaVu Sans', sans-serif` (a plain fallback where DejaVu is missing, e.g. browsers on macOS).
  Mathtext still emits 'DejaVu Sans' with oblique for math variables (correct).
- asyncio subprocesses are killed when their event loop shuts down (transport close()), so a job that
  must outlive the viewer can't use asyncio.create_subprocess_exec. Use Popen + a daemon thread instead.
- Delta allocation with open(path, "x") followed by a write raced against a concurrent allocator's delta_tree()
  check, which saw an empty file and raised malformed (a flaky test_concurrent_allocation_is_distinct under load).
  Fixed: write a temp file, then os.link() it into place (atomic and fails if the name exists).
- Preview-rendering tests skip via the `svg_converter` fixture only when publish.converter() finds nothing. Don't
  importorskip("cairosvg"): on the dev machine the test python has no cairosvg, and new_session strips PATH dirs
  containing `uv` (miniforge3/bin, which also holds rsvg-convert), so the tests render with /usr/bin/inkscape.
  pytest 9's importorskip skips only on ModuleNotFoundError; cairosvg without libcairo raises OSError.
- guard.sh used `printf ... | exec python3 turn_start.py` for the first /plotbook:start. The pipeline runs in a subshell,
  so the script fell through to `exit 0` and refusals (exit 2) never blocked the prompt. Now `| python3 ...; exit $?`.
- 2026-10-23: a stray /tmp/.plotbook/ (thread A drafts and archives from 10-21; not made by tests) made every pytest tmp
  repo look nested in an analysis. Left in place, since it may be the user's. Fixed for good by stopping find_root at `.git`.
- Font isolation silently didn't run for the live viewer (italic previews again): `uv run` puts the viewer env's bin first
  on PATH, so `python3` there was the viewer env (no matplotlib) and the lookup gave up. My earlier check passed only
  because I ran it from a shell where python3 was miniforge's. Fixed: matplotlib is in the viewer's deps (in-process
  import first), then every python3/python on PATH is tried. De-duplicate by abspath, NOT realpath: a venv python
  symlinks to its base interpreter, which has a different sys.path. Verify with the viewer env's interpreter and PATH.
