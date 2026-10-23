"""UserPromptSubmit hook: claim threads on `/plotbook:start`, record the turn, inject context.

stdout (exit 0) is added to the model's context; exit 2 blocks the prompt with stderr shown
to the user (used when a thread claim is refused).
"""

import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claim_thread  # noqa: E402
import pbcore  # noqa: E402

START_RE = re.compile(r"^\s*/plotbook:start(?:\s+(?P<args>.*?))?\s*$", re.S)
USAGE = ("usage: /plotbook:start <thread> [<analysis-dir>] [--steal]   (e.g. /plotbook:start primary analysis)\n"
         "<analysis-dir> is relative to where Claude Code is running; without it, the analysis is the directory\n"
         "Claude Code runs in (or the analysis containing it, or the one where <thread> was last claimed).")


def record_prompt(root, session_id, prompt):
    path = pbcore.turn_path(root, session_id)
    turn = pbcore.read_json(path) or {"prompts": []}
    turn["prompts"].append({"ts": pbcore.now_iso(), "text": prompt})
    pbcore.write_json(path, turn)


def context_line(root, thread):
    leaf = pbcore.thread_leaf(root, thread)
    return ('[plotbook] thread=%s dir=%s next=%s%04d leaf=%s; follow the plotbook:conventions skill; '
            'end file-producing turns with: plotbook-note %s "<one-line summary of the request>"'
            % (thread, root, thread, pbcore.next_number(root, thread),
               "shared_%04d" % leaf if leaf is not None else "none", thread))


def parse_start(args):
    """(thread, analysis_dir or None, steal) from the text after /plotbook:start; ValueError if malformed."""
    words = shlex.split(args or "")
    steal = "--steal" in words
    words = [w for w in words if w != "--steal"]
    if not words or len(words) > 2 or any(w.startswith("-") for w in words):
        raise ValueError(USAGE)
    return words[0], (words[1] if len(words) == 2 else None), steal


def start_root(cwd, sid, thread, analysis_dir):
    """The analysis directory `/plotbook:start` should use (may not exist yet). ValueError if unusable."""
    if analysis_dir is not None:
        target = os.path.realpath(os.path.join(cwd, os.path.expanduser(analysis_dir)))
    else:
        roots = pbcore.thread_roots(cwd, thread)
        target = (pbcore.find_root(cwd) or pbcore.registered_root(cwd, sid)
                  or (roots[0] if len(roots) == 1 else None) or os.path.realpath(cwd))
    existing = target
    while not os.path.isdir(existing):
        existing = os.path.dirname(existing)
    top = pbcore.git(["rev-parse", "--show-toplevel"], existing, check=False)
    if top.returncode != 0:
        raise ValueError("%s is not inside a git repository. Create the analysis branch (and its PR) first; "
                         "plotbook commits each turn to it." % target)
    top = os.path.realpath(top.stdout.strip())
    if os.path.relpath(target, top).startswith(".."):
        raise ValueError("%s is outside the git repository %s" % (target, top))
    enclosing = pbcore.find_root(target)
    if enclosing is not None and enclosing != target:
        raise ValueError("%s is inside the analysis %s; analyses can't nest. Use %s itself, or another directory."
                         % (target, enclosing, enclosing))
    return target


def start(inp, m):
    cwd = inp.get("cwd") or os.getcwd()
    sid = inp.get("session_id")
    try:
        thread, analysis_dir, steal = parse_start(m.group("args"))
        if not pbcore.valid_thread(thread):
            raise claim_thread.ClaimRefused(claim_thread.invalid_thread_message(thread))
        root = start_root(cwd, sid, thread, analysis_dir)
    except (ValueError, claim_thread.ClaimRefused) as err:
        print("plotbook: %s" % err, file=sys.stderr)
        return 2
    previous = pbcore.registered_root(cwd, sid)
    created = claim_thread.scaffold(root)
    try:
        released = claim_thread.claim(root, thread, sid, steal=steal)
    except claim_thread.ClaimRefused as err:
        print("plotbook: %s" % err, file=sys.stderr)
        return 2
    if previous and previous != root:  # this session moved to another analysis: let its old thread go
        released = claim_thread.release_session(previous, sid) or released
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
