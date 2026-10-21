"""UserPromptSubmit hook: claim threads on `/plotbook:start`, record the turn, inject context.

stdout (exit 0) is added to the model's context; exit 2 blocks the prompt with stderr shown
to the user (used when a thread claim is refused).
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claim_thread  # noqa: E402
import pbcore  # noqa: E402

START_RE = re.compile(r"^\s*/plotbook:start(?:\s+(?P<thread>[^\s-]\S*))?(?P<steal>\s+--steal)?\s*$")


def record_prompt(root, session_id, prompt):
    path = pbcore.turn_path(root, session_id)
    turn = pbcore.read_json(path) or {"prompts": []}
    turn["prompts"].append({"ts": pbcore.now_iso(), "text": prompt})
    pbcore.write_json(path, turn)


def context_line(root, thread):
    leaf = pbcore.thread_leaf(root, thread)
    return ('[plotbook] thread=%s next=%s%04d leaf=%s; follow the plotbook:conventions skill; '
            'end file-producing turns with: plotbook-note %s "<one-line summary of the request>"'
            % (thread, thread, pbcore.next_number(root, thread),
               "shared_%04d" % leaf if leaf is not None else "none", thread))


def start(inp, m):
    cwd = inp.get("cwd") or os.getcwd()
    sid = inp.get("session_id")
    thread = m.group("thread")
    if not thread:
        print("usage: /plotbook:start <thread>   (e.g. /plotbook:start primary)", file=sys.stderr)
        return 2
    root = pbcore.find_root(cwd) or os.path.realpath(cwd)
    if pbcore.git(["rev-parse", "--show-toplevel"], root, check=False).returncode != 0:
        print("plotbook: %s is not inside a git repository. Create the analysis branch (and its PR) "
              "first; plotbook commits each turn to it." % root, file=sys.stderr)
        return 2
    created = claim_thread.scaffold(root)
    try:
        released = claim_thread.claim(root, thread, sid, steal=bool(m.group("steal")))
    except claim_thread.ClaimRefused as err:
        print("plotbook: %s" % err, file=sys.stderr)
        return 2
    record_prompt(root, sid, inp.get("prompt", ""))
    for rel in created:
        pbcore.append_jsonl(pbcore.journal_path(root, sid), {"file": rel})
    url, note = claim_thread.ensure_viewer(root)
    lines = ['[plotbook] This session now owns thread "%s" in analysis %s.' % (thread, root)]
    if released:
        lines.append('[plotbook] Released this session\'s previous thread "%s".' % released)
    if created:
        lines.append("[plotbook] Scaffolded: %s (committed at the end of this turn)." % ", ".join(created))
    if url:
        lines.append("[plotbook] Viewer: %s/t/%s" % (url, thread) + (" (%s)" % note if note else ""))
    elif note:
        lines.append("[plotbook] %s" % note)
    lines.append(context_line(root, thread))
    print("\n".join(lines))
    return 0


def main():
    inp = pbcore.hook_input()
    prompt = inp.get("prompt", "")
    m = START_RE.match(prompt)
    if m:
        return start(inp, m)
    root = pbcore.hook_root(inp)
    sid = inp.get("session_id")
    if root is None or not sid:
        return 0
    thread = pbcore.session_thread(root, sid)
    resumed = thread is None
    if resumed:
        thread = claim_thread.reclaim_on_resume(root, sid)
    if thread is None:
        print("[plotbook] This session has not claimed a thread in analysis %s; nothing will be committed. "
              "The user can run /plotbook:start <thread> to begin." % root)
        return 0
    record_prompt(root, sid, prompt)
    # The viewer stops when the last session here ends; bring it back for a resumed session (or after a crash).
    url, note = claim_thread.ensure_viewer(root)
    if url and (note or resumed):
        print("[plotbook] Viewer: %s/t/%s" % (url, thread) + (" (%s)" % note if note else ""))
    elif note and resumed:
        print("[plotbook] %s" % note)
    print(context_line(root, thread))
    return 0


if __name__ == "__main__":
    sys.exit(main())
