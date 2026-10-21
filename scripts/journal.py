"""PostToolUse(Write|Edit|MultiEdit|NotebookEdit) hook: journal files this session wrote."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402


def main():
    inp = pbcore.hook_input()
    root = pbcore.hook_root(inp)
    sid = inp.get("session_id")
    if root is None or not sid or pbcore.session_thread(root, sid) is None:
        return 0
    ti = inp.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    if not path:
        return 0
    path = os.path.abspath(os.path.join(inp.get("cwd") or root, path))
    rel = os.path.relpath(path, root)
    if rel.startswith("..") or rel.startswith(os.path.join(".plotbook", "state")):
        return 0
    pbcore.append_jsonl(pbcore.journal_path(root, sid), {"file": rel})
    return 0


if __name__ == "__main__":
    sys.exit(main())
