"""Draft the "🤖 Extra details 🤖" addendum with a headless `claude -p` call; print it to stdout.

    addendum.py --root <analysis> --thread T

Called by the viewer when the draft view opens (and on Regenerate). The result goes into an
editable pane; what the user leaves there is published verbatim. Prompt template:
~/.config/plotbook/addendum-prompt.md if present, else the bundled templates/addendum-prompt.md.
Model: $PLOTBOOK_ADDENDUM_MODEL (default "sonnet").
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_LIMIT = 12000
TRANSCRIPT_LIMIT = 30000


def template():
    user = os.path.join(os.path.expanduser("~"), ".config", "plotbook", "addendum-prompt.md")
    path = user if os.path.exists(user) else os.path.join(PLUGIN_ROOT, "templates", "addendum-prompt.md")
    with open(path) as f:
        return f.read()


def context(root, thread):
    through = pbcore.last_through(root, thread)
    arts = [name for n, name in pbcore.artifacts(root, thread, "plots") if n > through]
    scripts = [name for n, name in pbcore.artifacts(root, thread, "scripts") if n > through]
    deltas = set()
    parts = []
    for name in scripts:
        path = os.path.join(root, "scripts", name)
        try:
            text = open(path).read()
        except OSError:
            continue
        try:
            leaf = pbcore.script_leaf_from_text(text)
        except pbcore.PlotbookError:
            leaf = None
        if leaf is not None:
            deltas.update(pbcore.delta_chain(pbcore.delta_tree(root), leaf))
        parts.append("--- scripts/%s ---\n%s" % (name, text[:SCRIPT_LIMIT]))
    for m in sorted(deltas):
        if m == 0:
            continue  # the root delta is plotbook boilerplate
        parts.append("--- scripts/shared_%04d.py ---\n%s" % (m, open(os.path.join(root, "scripts", "shared_%04d.py" % m)).read()[:SCRIPT_LIMIT]))
    records = []
    tdir = os.path.join(root, ".plotbook", "transcripts")
    for name in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
        if name.startswith(thread + "-"):
            records += [r for r in pbcore.read_jsonl(os.path.join(tdir, name)) if "plotbook_transcript" not in r]
    records.sort(key=lambda r: r.get("ts") or "")
    lines, size = [], 0
    for r in reversed(records):
        line = json.dumps(r, ensure_ascii=False)
        if size + len(line) > TRANSCRIPT_LIMIT:
            break
        lines.append(line)
        size += len(line) + 1
    return {"thread": thread,
            "artifacts": "\n".join("- plots/" + a for a in arts) or "(none)",
            "scripts": "\n\n".join(parts) or "(none)",
            "transcript": "\n".join(reversed(lines)) or "(none)"}


def build_prompt(root, thread):
    text = template()
    for key, value in context(root, thread).items():
        text = text.replace("{{%s}}" % key, value)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--thread", required=True)
    ap.add_argument("--print-prompt", action="store_true", help="print the prompt instead of calling claude")
    args = ap.parse_args()
    root = pbcore.find_root(args.root)
    if root is None:
        sys.exit("not a plotbook analysis: %s" % args.root)
    prompt = build_prompt(root, args.thread)
    if args.print_prompt:
        print(prompt)
        return 0
    model = os.environ.get("PLOTBOOK_ADDENDUM_MODEL", "sonnet")
    # Run from an empty temp dir with PLOTBOOK_HEADLESS so plotbook's own hooks stay out of it.
    with tempfile.TemporaryDirectory(prefix="plotbook-addendum-") as tmp:
        try:
            cp = subprocess.run(["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence"],
                                input=prompt, capture_output=True, text=True, timeout=600, cwd=tmp,
                                env=dict(os.environ, PLOTBOOK_HEADLESS="1"))
        except FileNotFoundError:
            sys.exit("the `claude` CLI is not on PATH; write the Extra details yourself or leave the pane empty")
        except subprocess.TimeoutExpired:
            sys.exit("claude -p timed out")
    if cp.returncode != 0:
        sys.exit("claude -p failed (%d): %s" % (cp.returncode, (cp.stderr or cp.stdout).strip()[-1500:]))
    print(cp.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
