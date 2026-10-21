"""Filter a Claude Code session transcript into .plotbook/transcripts/<thread>-<sid8>.jsonl.

The output is regenerated in full from the source JSONL each time, deterministically, so
git diffs of it are pure appends. The source format is internal to Claude Code and changes
between releases: any entry type or content-block type not in the tables below is a LOUD
failure (exit 1 + TRANSCRIPT-SYNC-BROKEN marker at the analysis root). Fix by adding the
new type to the table. Unknown *fields* on known types are ignored.

As a hook (PreCompact / SessionEnd backstop) it also commits the transcript.
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402

FORMAT_VERSION = 1

# ---- expectations table (verified against Claude Code 2.1.x, 2026-10) ---------------------
# Top-level entry "type" -> "keep" (filtered below) or "drop" (ignored entirely).
ENTRY_TYPES = {
    "user": "keep",
    "assistant": "keep",
    "system": "drop",  # turn_duration, compact_boundary, local_command, stop_hook_summary, ...
    "summary": "drop",
    "attachment": "drop",  # environment, prompt snapshots, hook output, user email, ...
    "file-history-snapshot": "drop",
    "file-history-delta": "drop",
    "queue-operation": "drop",
    "last-prompt": "drop",
    "mode": "drop",
    "permission-mode": "drop",
    "atis-latch": "drop",
    "ai-title": "drop",
    "custom-title": "drop",
    "agent-name": "drop",
    "cost-state": "drop",
    "bridge-session": "drop",
    "pr-link": "drop",
    "api-request": "drop",
    "api-request-blob": "drop",
    "api-request-shape": "drop",
}
# Content-block "type" inside message.content lists, per role -> "keep" / "drop".
BLOCK_TYPES = {
    "user": {"text": "keep", "tool_result": "drop", "image": "drop", "document": "drop"},
    "assistant": {"text": "keep", "tool_use": "keep", "thinking": "drop", "redacted_thinking": "drop",
                  "server_tool_use": "drop", "web_search_tool_result": "drop", "web_fetch_tool_result": "drop"},
}
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
RAN_LIMIT = 200
# User-entry flags meaning "not something the human typed or saw as dialogue".
SKIP_FLAGS = ("isMeta", "isSidechain", "isCompactSummary", "isVisibleInTranscriptOnly")
# --------------------------------------------------------------------------------------------

_DROP_USER_PREFIXES = ("<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>",
                       "<bash-stdout>", "<bash-stderr>")
_CMD_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_BASH_RE = re.compile(r"^<bash-input>(.*?)</bash-input>$", re.S)


class TranscriptFormatError(Exception):
    pass


def _user_text(text):
    s = text.strip()
    if not s or s.startswith(_DROP_USER_PREFIXES):
        return None
    m = _CMD_RE.search(s)
    if m:
        args = _ARGS_RE.search(s)
        return (m.group(1).strip() + (" " + args.group(1).strip() if args and args.group(1).strip() else ""))
    m = _BASH_RE.match(s)
    if m:
        return "! " + m.group(1).strip()
    return text


def _rel(root, path):
    if not path:
        return path
    ap = os.path.abspath(path)
    rel = os.path.relpath(ap, root)
    return ap if rel.startswith("..") else rel


def filter_lines(lines, root):
    out = []
    for lineno, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            # A torn final line while Claude Code is mid-write is normal; anything else is not.
            if lineno == len(lines):
                continue
            raise TranscriptFormatError("line %d: not valid JSON" % lineno)
        if not isinstance(e, dict) or "type" not in e:
            raise TranscriptFormatError("line %d: entry without a type" % lineno)
        etype = e["type"]
        action = ENTRY_TYPES.get(etype)
        if action is None:
            raise TranscriptFormatError("line %d: unknown entry type %r" % (lineno, etype))
        if action == "drop":
            continue
        if any(e.get(flag) for flag in SKIP_FLAGS):
            continue
        msg = e.get("message")
        if not isinstance(msg, dict) or "content" not in msg:
            raise TranscriptFormatError("line %d: %s entry without message.content" % (lineno, etype))
        ts = e.get("timestamp")
        content = msg["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            raise TranscriptFormatError("line %d: message.content is %s" % (lineno, type(content).__name__))
        texts = []
        for b in content:
            btype = b.get("type") if isinstance(b, dict) else None
            baction = BLOCK_TYPES[etype].get(btype)
            if baction is None:
                raise TranscriptFormatError("line %d: unknown %s content block type %r" % (lineno, etype, btype))
            if baction == "drop":
                continue
            if btype == "text":
                t = b.get("text", "")
                if etype == "user":
                    t = _user_text(t)
                if t and t.strip():
                    texts.append(t)
            elif btype == "tool_use":
                name, inp = b.get("name"), b.get("input") or {}
                if name in WRITE_TOOLS:
                    out.append({"event": "wrote", "ts": ts, "file": _rel(root, inp.get(WRITE_TOOLS[name]))})
                elif name == "Bash":
                    cmd = " ".join(str(inp.get("command", "")).split())
                    if len(cmd) > RAN_LIMIT:
                        cmd = cmd[: RAN_LIMIT - 1] + "…"
                    out.append({"event": "ran", "ts": ts, "command": cmd})
        if texts:
            out.append({"role": etype, "ts": ts, "text": "\n\n".join(texts)})
    return out


def _contains_text(path, want):
    try:
        with open(path) as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                for b in (e.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text") == want:
                        return True
    except OSError:
        pass
    return False


def wait_for_flush(source, last_assistant_message, timeout=3.0):
    """Claude Code writes the turn's final text asynchronously; wait briefly for it."""
    if not last_assistant_message:
        return True
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _contains_text(source, last_assistant_message):
            return True
        time.sleep(0.05)
    return False


def marker_path(root):
    return os.path.join(root, pbcore.MARKER)


def sync(root, thread, session_id, source, last_assistant_message=None):
    """Regenerate this session's filtered transcript. Returns its analysis-relative path.

    Raises TranscriptFormatError (after writing the marker file) on unrecognized content.
    """
    rel = pbcore.transcript_relpath(thread, session_id)
    if not source or not os.path.exists(source):
        raise TranscriptFormatError("source transcript not found: %r" % source)
    wait_for_flush(source, last_assistant_message)
    with open(source) as f:
        lines = f.readlines()
    try:
        records = filter_lines(lines, root)
    except TranscriptFormatError as err:
        with open(marker_path(root), "w") as f:
            f.write("plotbook could not filter a Claude Code transcript; its format has changed.\n"
                    "Committed transcripts are paused for this session until the filter is updated\n"
                    "(ENTRY_TYPES / BLOCK_TYPES at the top of plotbook's scripts/sync_transcript.py).\n"
                    "The raw transcript is intact; once fixed, the next sync recovers everything.\n\n"
                    "session: %s\nthread: %s\nsource: %s\nerror: %s\ntime: %s\n"
                    % (session_id, thread, source, err, pbcore.now_iso()))
        raise TranscriptFormatError("%s (source %s)" % (err, source))
    header = {"plotbook_transcript": FORMAT_VERSION, "session": session_id, "thread": thread}
    dest = os.path.join(root, rel)
    pbcore.ensure_dir(os.path.dirname(dest))
    tmp = dest + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as f:
        for rec in [header] + records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, dest)
    if os.path.exists(marker_path(root)):
        os.remove(marker_path(root))
    return rel


def main():
    """Hook entry point for PreCompact and SessionEnd: sync, commit transcript, release at end."""
    inp = pbcore.hook_input()
    root = pbcore.hook_root(inp)
    sid = inp.get("session_id")
    if root is None or not sid:
        return 0
    thread = pbcore.session_thread(root, sid)
    if thread is None:
        return 0
    status = 0
    try:
        rel = sync(root, thread, sid, inp.get("transcript_path"))
        pbcore.commit_paths(root, [rel], "[%s] transcript sync (%s)" % (thread, inp.get("hook_event_name", "backstop")),
                            "session: %s\nthread: %s" % (sid, thread))
    except TranscriptFormatError as err:
        print("plotbook: TRANSCRIPT-SYNC-BROKEN: %s" % err, file=sys.stderr)
        status = 1
    except pbcore.PlotbookError as err:
        print("plotbook: %s" % err, file=sys.stderr)
        status = 1
    if inp.get("hook_event_name") == "SessionEnd":
        claim = pbcore.read_claim(root, thread)
        if claim and claim.get("session_id") == sid:
            os.remove(pbcore.claim_path(root, thread))
    return status


if __name__ == "__main__":
    sys.exit(main())
