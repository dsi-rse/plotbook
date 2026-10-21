"""Stop hook: sync this session's transcript and commit exactly this turn's work.

Staged paths = journaled Write/Edit paths ∪ changed artifacts with this thread's prefix
(outputs are written by Bash-run Python and never appear in the journal) ∪ shared deltas
this thread allocated ∪ this session's filtered transcript. Never `git add -A`.

Exit 1 (never 2: that would force Claude to keep going) with stderr on failure; files are
committed even when the transcript filter fails.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402
import sync_transcript  # noqa: E402


def take_journal(root, sid):
    """Move the journal aside (so concurrent appends start a fresh one); return its files."""
    path = pbcore.journal_path(root, sid)
    pending = path + ".pending"
    if os.path.exists(path):
        if os.path.exists(pending):
            with open(path) as src, open(pending, "a") as dst:
                dst.write(src.read())
            os.remove(path)
        else:
            os.replace(path, pending)
    return pending, [r["file"] for r in pbcore.read_jsonl(pending) if r.get("file")]


def compose_message(root, thread, sid, paths):
    turn = pbcore.read_json(pbcore.turn_path(root, sid)) or {"prompts": []}
    prompts = [p["text"] for p in turn.get("prompts", []) if p.get("text")]
    note = None
    try:
        with open(pbcore.note_path(root, thread)) as f:
            note = pbcore.one_line(f.read(), 72) or None
    except OSError:
        pass
    summary = note or pbcore.one_line(prompts[-1] if prompts else "(no prompt recorded)", 50)
    subject = "%s %s" % (pbcore.artifact_range(paths, thread), summary)
    body = []
    for p in prompts:
        body.append("Prompt:\n" + p.strip())
    body.append("session: %s\nthread: %s" % (sid, thread))
    return subject, "\n\n".join(body)


def main():
    inp = pbcore.hook_input()
    root = pbcore.hook_root(inp)
    sid = inp.get("session_id")
    if root is None or not sid:
        return 0
    thread = pbcore.session_thread(root, sid)
    if thread is None:
        return 0
    errors = []
    paths = []
    try:
        paths.append(sync_transcript.sync(root, thread, sid, inp.get("transcript_path"),
                                          inp.get("last_assistant_message")))
    except sync_transcript.TranscriptFormatError as err:
        errors.append("TRANSCRIPT-SYNC-BROKEN: %s" % err)
        rel = pbcore.transcript_relpath(thread, sid)
        if os.path.exists(os.path.join(root, rel)):
            paths.append(rel)  # keep committing whatever was synced before the break (unchanged)
    pending, journaled = take_journal(root, sid)
    paths = pbcore.confine(root, journaled + pbcore.thread_owned_changes(root, thread) + paths)
    subject, body = compose_message(root, thread, sid, paths)
    try:
        pbcore.commit_paths(root, paths, subject, body)
    except pbcore.PlotbookError as err:
        errors.append("commit failed (work is safe in the working tree; the next turn retries): %s" % err)
    else:
        for p in (pending, pbcore.turn_path(root, sid), pbcore.note_path(root, thread)):
            if os.path.exists(p):
                os.remove(p)
    if errors:
        print("plotbook: " + "\nplotbook: ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
