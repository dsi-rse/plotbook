"""PreToolUse(Write|Edit|MultiEdit|NotebookEdit) hook: enforce plotbook's append-only rules.

Denies (with a reason the model sees):
  - edits to hook-managed files (.plotbook/transcripts, previews, published.jsonl, .gitignore);
  - edits to shared deltas that are committed, not allocated via plotbook-delta, or another
    thread's; non-canonical first lines in deltas;
  - edits to another thread's numbered artifacts, or to artifacts already published;
  - non-canonical exec lines in scripts.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402

MANAGED = (os.path.join(".plotbook", "transcripts") + os.sep, os.path.join(".plotbook", "previews") + os.sep)
MANAGED_FILES = (os.path.join(".plotbook", "published.jsonl"), os.path.join(".plotbook", ".gitignore"))


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "plotbook: " + reason}}))
    return 0


def new_text(tool, ti):
    if tool == "Write":
        return ti.get("content", "")
    if tool == "Edit":
        return ti.get("new_string", "")
    if tool == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in ti.get("edits", []))
    return ""


def in_head(root, rel):
    return pbcore.git(["rev-parse", "--verify", "-q", "HEAD:./" + rel], root, check=False).returncode == 0


def check(root, thread, tool, ti):
    path = ti.get("file_path") or ti.get("notebook_path")
    if not path:
        return None
    rel = os.path.relpath(os.path.abspath(path), root)
    if rel.startswith(".."):
        return None
    if rel.startswith(MANAGED) or rel in MANAGED_FILES:
        return "%s is managed by plotbook's hooks and publish pipeline; never edit it." % rel
    name = os.path.basename(rel)
    sub = os.path.dirname(rel)
    text = new_text(tool, ti)

    mo = pbcore.DELTA_NAME_RE.match(name)
    if sub == "scripts" and mo:
        m = int(mo.group(1))
        abspath = os.path.join(root, rel)
        alloc_hint = "allocate a new delta with: plotbook-delta --thread %s --parent <NNNN>" % (thread or "<thread>")
        if not os.path.exists(abspath):
            return "shared deltas must be allocated, not created directly; %s" % alloc_hint
        if in_head(root, rel):
            return ("%s is committed and deltas are append-only; put changes (re-emitted functions) in a new "
                    "delta instead. %s" % (rel, alloc_hint))
        owner = pbcore.delta_owners(root).get(m)
        if owner is not None and thread is not None and owner != thread:
            return "%s belongs to thread %s; %s" % (rel, owner, alloc_hint)
        if tool == "Write" and m > 0:
            first = text.split("\n", 1)[0]
            if pbcore.match_exec_line(first, pbcore.exec_style(root)) is None:
                with open(abspath) as f:
                    expected = f.readline().rstrip("\n")
                return "the first line of %s must stay exactly %s" % (rel, expected)
        return None

    art = pbcore.parse_artifact(name)
    if art and sub in ("scripts", "plots"):
        if thread is not None and art[0] != thread:
            return ("%s belongs to thread %s, and threads are single-writer; this session is thread %s. "
                    "Make a new %s artifact instead." % (rel, art[0], thread, thread))
        published = {(pbcore.parse_artifact(os.path.basename(a)) or (None, None))[:2]
                     for rec in pbcore.published(root) for a in rec.get("artifacts", [])}
        if (art[0], art[1]) in published:
            return ("%s%04d has been published in a PR comment and is immutable; make the correction as "
                    "a new number (next: %s%04d)." % (art[0], art[1], art[0], pbcore.next_number(root, art[0])))
    if sub == "scripts" and name.endswith(".py") and text:
        try:
            style = pbcore.exec_style(root)
            if tool == "Write":
                pbcore.script_leaf_from_text(text, rel, style)
            else:
                for i, line in enumerate(text.splitlines(), 1):
                    if pbcore.EXEC_LIKE_RE.match(line) and pbcore.match_exec_line(line, style) is None:
                        raise pbcore.PlotbookError("%s: exec line must be exactly %s"
                                                   % (rel, pbcore.canonical_exec_line("NNNN", style)))
        except pbcore.PlotbookError as err:
            return str(err)
    return None


def main():
    inp = pbcore.hook_input()
    ti = dict(inp.get("tool_input") or {})
    key = "file_path" if ti.get("file_path") else "notebook_path"
    if not ti.get(key):
        return 0
    # The rules belong to the analysis the file is in, wherever this session runs or is registered.
    ti[key] = os.path.abspath(os.path.join(inp.get("cwd") or os.getcwd(), ti[key]))
    root = pbcore.find_root(os.path.dirname(ti[key]))
    if root is None:
        return 0
    thread = pbcore.session_thread(root, inp.get("session_id")) if inp.get("session_id") else None
    try:
        reason = check(root, thread, inp.get("tool_name"), ti)
    except (OSError, pbcore.PlotbookError) as err:
        print("plotbook guard error (allowing): %s" % err, file=sys.stderr)
        return 0
    if reason:
        return deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
