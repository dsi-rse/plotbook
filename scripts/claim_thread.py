"""Thread claims, analysis scaffolding, and idempotent viewer start.

One live session per thread: claims live in .plotbook/state/claims/<thread>.json and are
refused while the claiming Claude Code process is alive (override with --steal).
Called from turn_start.py when the user types `/plotbook:start <thread>`.
"""

import fcntl
import glob
import os
import shutil
import signal
import socket
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GITIGNORE = "# plotbook: local-only state (claims, journals, drafts, viewer port)\nstate/\n"


class ClaimRefused(Exception):
    pass


def scaffold(root):
    """Create the three-entry layout; return analysis-relative paths of files created."""
    created = []
    for d in ("plots", "scripts", ".plotbook/transcripts", ".plotbook/previews", ".plotbook/state"):
        pbcore.ensure_dir(os.path.join(root, d))
    gi = os.path.join(root, ".plotbook", ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write(GITIGNORE)
        created.append(".plotbook/.gitignore")
    root_delta = os.path.join(root, "scripts", "shared_0000.py")
    if not os.path.exists(root_delta):
        shutil.copyfile(os.path.join(PLUGIN_ROOT, "templates", "shared_0000.py"), root_delta)
        created.append("scripts/shared_0000.py")
    return created


def session_record_path(root, session_id):
    return pbcore.state_dir(root, "sessions", session_id + ".thread")


def invalid_thread_message(thread):
    return ("invalid thread name %r: use letters (and inner - or _), starting and ending with a letter; "
            "'shared' is reserved" % thread)


def claim(root, thread, session_id, steal=False):
    """Claim `thread` for `session_id`. Raises ClaimRefused if a live session holds it."""
    if not pbcore.valid_thread(thread):
        raise ClaimRefused(invalid_thread_message(thread))
    path = pbcore.claim_path(root, thread)
    pbcore.ensure_dir(os.path.dirname(path))
    lock_fd = os.open(pbcore.state_dir(root, "claims", ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        existing = pbcore.read_json(path)
        if existing and existing.get("session_id") != session_id and not steal:
            if pbcore.pid_alive(existing.get("claude_pid")):
                raise ClaimRefused(
                    "thread %r is already claimed by live session %s (pid %s, since %s). "
                    "Use another thread name, or `/plotbook:start %s --steal` if that session is gone."
                    % (thread, existing.get("session_id"), existing.get("claude_pid"), existing.get("ts"), thread))
        # A session owns at most one thread: release any other claim it holds.
        other = pbcore.session_thread(root, session_id)
        if other and other != thread:
            os.remove(pbcore.claim_path(root, other))
        pbcore.write_json(path, {"session_id": session_id, "claude_pid": pbcore.claude_pid(), "ts": pbcore.now_iso()})
        rec = session_record_path(root, session_id)
        pbcore.ensure_dir(os.path.dirname(rec))
        with open(rec, "w") as f:
            f.write(thread + "\n")
    finally:
        os.close(lock_fd)
    pbcore.register_session(root, session_id, thread)
    return other if other and other != thread else None


def release_session(root, session_id):
    """Release whatever thread `session_id` holds in `root`; returns that thread or None."""
    thread = pbcore.session_thread(root, session_id)
    if thread is not None:
        os.remove(pbcore.claim_path(root, thread))
    return thread


def reclaim_on_resume(root, session_id):
    """A resumed session gets its old thread back if nobody live holds it. Returns thread or None."""
    try:
        with open(session_record_path(root, session_id)) as f:
            thread = f.read().strip()
    except OSError:
        return None
    try:
        claim(root, thread, session_id)
    except ClaimRefused:
        return None
    return thread


# ---------------------------------------------------------------- viewer


def _probe(port, root):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=0.5) as r:
            return r.read().decode().strip() == os.path.realpath(root)
    except Exception:
        return False


def _is_viewer_pid(pid, root=None):
    """True if `pid` is alive and is (a uv wrapper of) plotbook's viewer.py -- e.g. still starting.

    With `root`, it must also be the viewer for that analysis.
    """
    if not pbcore.pid_alive(pid):
        return False
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            args = f.read().decode(errors="replace")
    except OSError:
        try:
            args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True,
                                  timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return False
    return "viewer.py" in args and (root is None or os.path.realpath(root) in args)


def _free_port(prefer=None):
    """`prefer` (the viewer's previous port, so open browser tabs reconnect) if free, else any."""
    for port in ([prefer] if prefer else []) + [0]:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()


def ensure_viewer(root):
    """Start the viewer for this analysis unless one is already serving it. Returns (url, note)."""
    info_path = pbcore.state_dir(root, "viewer.json")
    pbcore.ensure_dir(os.path.dirname(info_path))
    lock_fd = os.open(pbcore.state_dir(root, "viewer.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        info = pbcore.read_json(info_path) or {}
        if info.get("port") and (_probe(info["port"], root) or _is_viewer_pid(info.get("pid"))):
            return "http://127.0.0.1:%d" % info["port"], None
        uv = shutil.which("uv")
        if uv is None:
            return None, ("viewer not started: `uv` is not installed (https://docs.astral.sh/uv/). "
                          "Everything still works; browse plots/ with any file manager.")
        port = _free_port(info.get("port"))
        log = open(pbcore.state_dir(root, "viewer.log"), "ab")
        proc = subprocess.Popen(
            [uv, "run", "--quiet", "--script", os.path.join(PLUGIN_ROOT, "server", "viewer.py"),
             "--root", os.path.realpath(root), "--port", str(port)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd=root,
            env=dict(os.environ, PLOTBOOK_PLUGIN_ROOT=PLUGIN_ROOT))
        pbcore.write_json(info_path, {"pid": proc.pid, "port": port, "started": pbcore.now_iso()})
        return "http://127.0.0.1:%d" % port, "viewer starting (first start may take a few seconds while uv fetches dependencies)"
    finally:
        os.close(lock_fd)


def stop_viewer(root, ending_session):
    """Stop this analysis's viewer unless a session other than `ending_session` is still live here.

    The viewer serves every thread of the analysis, so it lives as long as any claimed session.
    Only the viewer process is signalled: a publish it launched runs to completion on its own.
    Returns True if a viewer was stopped.
    """
    for path in glob.glob(pbcore.state_dir(root, "claims", "*.json")):
        c = pbcore.read_json(path) or {}
        if c.get("session_id") != ending_session and pbcore.pid_alive(c.get("claude_pid")):
            return False
    lock_fd = os.open(pbcore.state_dir(root, "viewer.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        pid = (pbcore.read_json(pbcore.state_dir(root, "viewer.json")) or {}).get("pid")
        if not _is_viewer_pid(pid, root):
            return False
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True
    finally:
        os.close(lock_fd)


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="claim_thread")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("release", help="release a thread claim (e.g. after a crash)")
    r.add_argument("thread")
    v = sub.add_parser("viewer", help="ensure the viewer is running and print (and optionally open) its URL")
    v.add_argument("thread", nargs="?")
    v.add_argument("--draft", action="store_true", help="the thread's draft-comment view")
    v.add_argument("--open", action="store_true", help="also open it in a browser when one is available")
    args = ap.parse_args(argv)
    try:
        root = pbcore.tool_root(args.thread)
    except pbcore.PlotbookError as err:
        sys.exit("plotbook: %s" % err)
    if args.cmd == "release":
        p = pbcore.claim_path(root, args.thread)
        if os.path.exists(p):
            os.remove(p)
        print("released", args.thread)
    else:
        url, note = ensure_viewer(root)
        if url is None:
            sys.exit(note)
        if args.thread:
            url += "/t/" + args.thread + ("/draft" if args.draft else "")
        print(url + (" (%s)" % note if note else ""))
        if args.open and (sys.platform == "darwin" or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            import webbrowser

            webbrowser.open(url)


if __name__ == "__main__":
    main()
