"""Allocate the next shared delta: scripts/shared_NNNN.py with its canonical parent line.

usage: plotbook-delta --thread T --parent NNNN

Numbers are global across threads; allocation is atomic (hard-link of a fully written temp
file, which fails if the name exists) and rolls forward past numbers other sessions claimed
concurrently.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402


def allocate(root, thread, parent):
    scripts = os.path.join(root, "scripts")
    parent_file = os.path.join(scripts, "shared_%04d.py" % parent)
    if not os.path.exists(parent_file):
        raise pbcore.PlotbookError("parent scripts/shared_%04d.py does not exist" % parent)
    pbcore.delta_tree(root)  # loud failure if the existing tree is malformed
    existing = [int(m.group(1)) for m in (pbcore.DELTA_NAME_RE.match(n) for n in os.listdir(scripts)) if m]
    m = max(existing, default=-1) + 1
    # Write a private temp file, then hard-link it into place: the link fails if the name is
    # taken (atomic claim, like open(..., "x")), and the file never appears without its first
    # line, so a concurrent allocator's delta_tree() check can't see it empty.
    tmp = os.path.join(scripts, ".shared-alloc-%d.tmp" % os.getpid())
    with open(tmp, "w") as f:
        f.write(pbcore.canonical_exec_line(parent) + "\n")
    try:
        while True:
            try:
                os.link(tmp, os.path.join(scripts, "shared_%04d.py" % m))
                break
            except FileExistsError:
                m += 1
    finally:
        os.remove(tmp)
    pbcore.append_jsonl(pbcore.state_dir(root, "deltas.jsonl"),
                        {"m": m, "parent": parent, "thread": thread, "ts": pbcore.now_iso()})
    return m, os.path.join("scripts", "shared_%04d.py" % m)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="plotbook-delta", description=__doc__.splitlines()[0])
    ap.add_argument("--thread", required=True)
    ap.add_argument("--parent", required=True, help="parent delta number, e.g. 0007 or 7")
    args = ap.parse_args(argv)
    root = pbcore.find_root(os.getcwd())
    if root is None:
        sys.exit("plotbook-delta: not inside a plotbook analysis (no .plotbook/ above %s)" % os.getcwd())
    if not pbcore.valid_thread(args.thread):
        sys.exit("plotbook-delta: invalid thread name %r" % args.thread)
    try:
        parent = int(os.path.basename(args.parent).replace("shared_", "").replace(".py", ""))
        m, rel = allocate(root, args.thread, parent)
    except (pbcore.PlotbookError, ValueError) as err:
        sys.exit("plotbook-delta: %s" % err)
    print(rel)
    print("Its first line is already %s; add only the changed definitions below it." % pbcore.canonical_exec_line(parent))
    print("Plot scripts pin it with: %s" % pbcore.canonical_exec_line(m))


if __name__ == "__main__":
    main()
