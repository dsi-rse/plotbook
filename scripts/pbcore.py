"""Shared core for plotbook's hooks and tools.

Stdlib only; must run under any python3 >= 3.9 (hooks use the system python3).
Everything that touches git goes through `git()` here, called only from hook
scripts and the publish/repro pipelines -- never from the model.
"""

import contextlib
import errno
import fcntl
import json
import os
import re
import subprocess
import sys
import time

THREAD_RE = re.compile(r"^(?:[A-Za-z][A-Za-z_-]*[A-Za-z]|[A-Za-z])$")
RESERVED_THREADS = {"shared"}
ARTIFACT_RE = re.compile(
    r"^(?P<thread>[A-Za-z](?:[A-Za-z_-]*[A-Za-z])?)(?P<n>\d{4})-(?P<slug>.+)\.(?P<ext>[A-Za-z0-9]+)$"
)
DELTA_NAME_RE = re.compile(r"^shared_(\d{4})\.py$")
DELTA_LINE_RE = re.compile(r'^exec\(open\("scripts/shared_(\d{4})\.py"\)\.read\(\)\)\s*$')
# Anything that looks like an attempt at an exec line, canonical or not.
EXEC_LIKE_RE = re.compile(r"^\s*exec\s*\(\s*open\s*\(")

MARKER = "TRANSCRIPT-SYNC-BROKEN"


class PlotbookError(Exception):
    pass


# ---------------------------------------------------------------- layout


def find_root(start):
    """Nearest ancestor of `start` (inclusive) containing a `.plotbook/` dir, or None."""
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".plotbook")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def state_dir(root, *parts):
    return os.path.join(root, ".plotbook", "state", *parts)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def valid_thread(name):
    return bool(THREAD_RE.match(name)) and name not in RESERVED_THREADS


def canonical_exec_line(m):
    return 'exec(open("scripts/shared_%04d.py").read())' % m


def parse_artifact(name):
    """Return (thread, n, slug, ext) for an artifact filename, else None."""
    mo = ARTIFACT_RE.match(name)
    if not mo or mo.group("thread") in RESERVED_THREADS:
        return None
    return mo.group("thread"), int(mo.group("n")), mo.group("slug"), mo.group("ext")


def artifacts(root, thread=None, subdir="plots"):
    """Sorted list of (n, filename) for artifacts in `subdir`, optionally one thread."""
    out = []
    d = os.path.join(root, subdir)
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        p = parse_artifact(name)
        if p and (thread is None or p[0] == thread):
            out.append((p[1], name))
    out.sort()
    return out


def next_number(root, thread):
    nums = [n for n, _ in artifacts(root, thread, "plots")]
    nums += [n for n, _ in artifacts(root, thread, "scripts")]
    return max(nums, default=0) + 1


def threads_present(root):
    found = set()
    for sub in ("plots", "scripts"):
        for name in os.listdir(os.path.join(root, sub)) if os.path.isdir(os.path.join(root, sub)) else []:
            p = parse_artifact(name)
            if p:
                found.add(p[0])
    claims = state_dir(root, "claims")
    if os.path.isdir(claims):
        for name in os.listdir(claims):
            if name.endswith(".json"):
                found.add(name[:-5])
    return sorted(found)


# ---------------------------------------------------------------- delta tree


def delta_parent(path):
    """Parent number of a delta file (None for the root), from its first line.

    Raises PlotbookError if the first line looks like an exec line but is not canonical,
    or if a non-root delta lacks a parent line.
    """
    name = os.path.basename(path)
    mo = DELTA_NAME_RE.match(name)
    if not mo:
        raise PlotbookError("not a shared delta: %s" % path)
    with open(path) as f:
        first = f.readline()
    m = DELTA_LINE_RE.match(first)
    if m:
        parent = int(m.group(1))
        if parent >= int(mo.group(1)):
            raise PlotbookError("%s: parent shared_%04d is not older than the delta" % (path, parent))
        return parent
    if int(mo.group(1)) == 0:
        return None
    raise PlotbookError(
        "%s: first line must be exactly %s (got %r)"
        % (path, 'exec(open("scripts/shared_NNNN.py").read())', first.rstrip("\n"))
    )


def delta_tree(root):
    """{m: parent_or_None} for every delta; loud error on a malformed line or missing parent."""
    d = os.path.join(root, "scripts")
    tree = {}
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        mo = DELTA_NAME_RE.match(name)
        if mo:
            tree[int(mo.group(1))] = delta_parent(os.path.join(d, name))
    for m, parent in tree.items():
        if parent is not None and parent not in tree:
            raise PlotbookError("scripts/shared_%04d.py: parent scripts/shared_%04d.py does not exist" % (m, parent))
    return tree


def delta_chain(tree, leaf):
    """Root-first list of delta numbers that `leaf` loads."""
    chain = []
    m = leaf
    while m is not None:
        if m not in tree:
            raise PlotbookError("scripts/shared_%04d.py does not exist" % m)
        chain.append(m)
        m = tree[m]
    return chain[::-1]


def script_leaf_from_text(text, where="script"):
    """The single delta number a plot script pins, or None if it pins nothing."""
    leaves = []
    for i, line in enumerate(text.splitlines(), 1):
        if EXEC_LIKE_RE.match(line):
            m = DELTA_LINE_RE.match(line)
            if not m:
                raise PlotbookError(
                    "%s line %d: exec line must be exactly "
                    'exec(open("scripts/shared_NNNN.py").read()) at top level' % (where, i)
                )
            leaves.append(int(m.group(1)))
    if len(leaves) > 1:
        raise PlotbookError("%s pins more than one shared delta: %s" % (where, leaves))
    return leaves[0] if leaves else None


def script_leaf(path):
    with open(path) as f:
        return script_leaf_from_text(f.read(), path)


def thread_leaf(root, thread):
    """Leaf pinned by the thread's most recent script; else the newest delta; else None."""
    for _, name in reversed(artifacts(root, thread, "scripts")):
        if name.endswith(".py"):
            try:
                leaf = script_leaf(os.path.join(root, "scripts", name))
            except (PlotbookError, OSError):
                continue
            if leaf is not None:
                return leaf
    d = os.path.join(root, "scripts")
    nums = [int(m.group(1)) for m in (DELTA_NAME_RE.match(n) for n in os.listdir(d)) if m] if os.path.isdir(d) else []
    return max(nums) if nums else None


def delta_owners(root):
    """{m: thread} from state/deltas.jsonl."""
    out = {}
    for rec in read_jsonl(state_dir(root, "deltas.jsonl")):
        out[rec["m"]] = rec["thread"]
    return out


# ---------------------------------------------------------------- json helpers


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    ensure_dir(os.path.dirname(path))
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def read_jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def append_jsonl(path, obj):
    ensure_dir(os.path.dirname(path))
    # One write() of one short line with O_APPEND: safe against concurrent appenders.
    data = (json.dumps(obj, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def hook_input():
    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}


def hook_root(inp):
    return find_root(inp.get("cwd") or os.getcwd())


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------- claims / sessions


def _proc_parent_and_name(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            data = f.read()
        name = data[data.index("(") + 1 : data.rindex(")")]
        ppid = int(data[data.rindex(")") + 2 :].split()[1])
        return ppid, name
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        ppid, name = out.split(None, 1)
        return int(ppid), os.path.basename(name)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None


def claude_pid():
    """PID of the nearest ancestor process that looks like Claude Code, else our parent."""
    pid = os.getppid()
    for _ in range(12):
        ppid, name = _proc_parent_and_name(pid)
        if name is None:
            break
        if "claude" in name.lower():
            return pid
        if not ppid or ppid == pid:
            break
        pid = ppid
    return os.getppid()


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError as err:
        return err.errno == errno.EPERM
    return True


def claim_path(root, thread):
    return state_dir(root, "claims", thread + ".json")


def read_claim(root, thread):
    return read_json(claim_path(root, thread))


def session_thread(root, session_id):
    """Thread claimed by this session, or None."""
    d = state_dir(root, "claims")
    if not os.path.isdir(d):
        return None
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            c = read_json(os.path.join(d, name))
            if c and c.get("session_id") == session_id:
                return name[:-5]
    return None


def sid8(session_id):
    return (session_id or "nosession")[:8]


def transcript_relpath(thread, session_id):
    return os.path.join(".plotbook", "transcripts", "%s-%s.jsonl" % (thread, sid8(session_id)))


def turn_path(root, session_id):
    return state_dir(root, "sessions", session_id + ".turn.json")


def journal_path(root, session_id):
    return state_dir(root, "sessions", session_id + ".journal.jsonl")


def note_path(root, thread):
    return state_dir(root, "note-%s.txt" % thread)


# ---------------------------------------------------------------- published manifest


def published(root):
    return read_jsonl(os.path.join(root, ".plotbook", "published.jsonl"))


def published_throughs(root, thread):
    """For each of the thread's comments, in publish order, the highest artifact number it covered."""
    out = []
    for rec in published(root):
        if rec.get("thread") == thread:
            through = rec.get("through", 0)
            if not through:  # entries written before `through` existed
                nums = [parse_artifact(os.path.basename(a))[1] for a in rec.get("artifacts", [])
                        if parse_artifact(os.path.basename(a))]
                through = max(nums, default=0)
            out.append(through)
    return out


def last_through(root, thread):
    """Highest artifact number covered by the thread's latest comment (0 if none)."""
    throughs = published_throughs(root, thread)
    return throughs[-1] if throughs else 0


def published_stems(root):
    stems = set()
    for rec in published(root):
        for a in rec.get("artifacts", []):
            stems.add(os.path.splitext(os.path.basename(a))[0])
    return stems


# ---------------------------------------------------------------- git


def git(args, cwd, check=True, input=None, retries=5):
    """Run git with retry on a transient index.lock; returns CompletedProcess."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    delay = 0.2
    for attempt in range(retries + 1):
        cp = subprocess.run(
            ["git"] + list(args), cwd=cwd, capture_output=True, text=True, input=input, env=env
        )
        if cp.returncode != 0 and "index.lock" in cp.stderr and attempt < retries:
            time.sleep(delay)
            delay *= 2
            continue
        break
    if check and cp.returncode != 0:
        raise PlotbookError("git %s failed:\n%s" % (" ".join(args), cp.stderr.strip() or cp.stdout.strip()))
    return cp


def git_out(args, cwd):
    return git(args, cwd).stdout.strip()


def head_sha(cwd):
    cp = git(["rev-parse", "--verify", "-q", "HEAD"], cwd, check=False)
    return cp.stdout.strip() or None


@contextlib.contextmanager
def repo_lock(cwd, timeout=120):
    """Repo-wide flock in the git common dir; serializes all plotbook git mutation."""
    common = git_out(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    path = os.path.join(common, "plotbook.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        t0 = time.time()
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() - t0 > timeout:
                    raise PlotbookError("timed out waiting for %s" % path)
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _literal(paths):
    return [":(literal)" + p for p in paths]


def changed_paths(root, pathspecs=("plots", "scripts")):
    """Analysis-relative paths matching `pathspecs` that are untracked, modified, deleted, or staged."""
    specs = _literal(pathspecs)
    out = set()
    cp = git(["ls-files", "-z", "--others", "--modified", "--deleted", "--exclude-standard", "--"] + specs, root, check=False)
    out.update(p for p in cp.stdout.split("\0") if p)
    if head_sha(root):
        cp = git(["diff", "--cached", "--name-only", "--relative", "-z", "HEAD", "--"] + specs, root, check=False)
        out.update(p for p in cp.stdout.split("\0") if p)
    return sorted(out)


def thread_owned_changes(root, thread):
    """Changed files this thread owns: its prefixed artifacts and the deltas it allocated."""
    owners = delta_owners(root)
    paths = []
    for p in changed_paths(root):
        name = os.path.basename(p)
        a = parse_artifact(name)
        if a and a[0] == thread:
            paths.append(p)
            continue
        mo = DELTA_NAME_RE.match(name)
        if mo and owners.get(int(mo.group(1))) == thread:
            paths.append(p)
    return paths


def confine(root, paths):
    """Analysis-relative, de-duplicated paths inside root, excluding state and the marker."""
    out = []
    for p in paths:
        ap = os.path.abspath(os.path.join(root, p))
        rel = os.path.relpath(ap, root)
        if rel.startswith("..") or os.path.isabs(rel):
            continue
        if rel == MARKER or rel.startswith(os.path.join(".plotbook", "state")):
            continue
        if rel not in out:
            out.append(rel)
    return out


def artifact_range(paths, thread):
    nums = sorted({parse_artifact(os.path.basename(p))[1] for p in paths
                   if parse_artifact(os.path.basename(p)) and parse_artifact(os.path.basename(p))[0] == thread})
    if not nums:
        return "[%s]" % thread
    if nums[0] == nums[-1]:
        return "[%s%04d]" % (thread, nums[0])
    return "[%s%04d–%s%04d]" % (thread, nums[0], thread, nums[-1])


def one_line(text, limit=60):
    text = " ".join((text or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def commit_paths(root, paths, subject, body=""):
    """Stage exactly `paths` (analysis-relative) and commit only them. Returns sha or None."""
    paths = confine(root, paths)
    if not paths:
        return None
    with repo_lock(root):
        # Only paths that exist or are known to git can be named to `git add`.
        known = set(p for p in git(["ls-files", "-z", "--"] + _literal(paths), root, check=False).stdout.split("\0") if p)
        addable = [p for p in paths if os.path.lexists(os.path.join(root, p)) or p in known]
        if not addable:
            return None
        git(["add", "--"] + _literal(addable), root)
        if head_sha(root):
            if git(["diff", "--cached", "--quiet", "HEAD", "--"] + _literal(addable), root, check=False).returncode == 0:
                return None
        args = ["commit", "--only", "--no-edit", "-m", subject]
        if body:
            args += ["-m", body]
        git(args + ["--"] + _literal(addable), root)
        return head_sha(root)


MANIFEST = os.path.join(".plotbook", "published.jsonl")


def thread_transcript_changes(root, thread):
    """This thread's transcripts (any session) that differ from HEAD."""
    return [p for p in changed_paths(root, [os.path.join(".plotbook", "transcripts")])
            if os.path.basename(p).startswith(thread + "-")]


def manifest_changes(root, thread):
    """[MANIFEST] if it has uncommitted entries published by `thread`, else [].

    Publishing appends the manifest entry but doesn't commit it (the comment URL only exists
    after the pinned commit is pushed); it rides along in the thread's next commit.
    """
    if MANIFEST not in changed_paths(root, [MANIFEST]):
        return []
    committed = git(["show", "HEAD:./" + MANIFEST], root, check=False).stdout.splitlines()
    for line in read_text_lines(os.path.join(root, MANIFEST)):
        if line not in committed:
            try:
                if json.loads(line).get("thread") == thread:
                    return [MANIFEST]
            except ValueError:
                continue
    return []


def read_text_lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except OSError:
        return []


def thread_pending(root, thread):
    """Everything uncommitted that belongs to `thread`: artifacts, owned deltas, transcripts, manifest."""
    return thread_owned_changes(root, thread) + thread_transcript_changes(root, thread) + manifest_changes(root, thread)
