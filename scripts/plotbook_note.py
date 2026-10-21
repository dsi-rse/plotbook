"""plotbook-note <thread> "<summary>": set the commit subject for this turn's commit.

The summary is a one-line restatement of what the user asked for, in their own register
(e.g. "move legend to upper right"); the Stop hook prefixes the artifact range.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402


def main(argv):
    if len(argv) < 2 or not argv[1].strip():
        sys.exit('usage: plotbook-note <thread> "<one-line summary of the request>"')
    thread, summary = argv[0], " ".join(argv[1:])
    root = pbcore.find_root(os.getcwd())
    if root is None:
        sys.exit("plotbook-note: not inside a plotbook analysis (no .plotbook/ above %s)" % os.getcwd())
    if not os.path.exists(pbcore.claim_path(root, thread)):
        sys.exit("plotbook-note: thread %r is not claimed in %s" % (thread, root))
    summary = pbcore.one_line(summary, 72)
    path = pbcore.note_path(root, thread)
    pbcore.ensure_dir(os.path.dirname(path))
    with open(path, "w") as f:
        f.write(summary + "\n")
    print("plotbook: this turn's commit subject will end with: %s" % summary)


if __name__ == "__main__":
    main(sys.argv[1:])
