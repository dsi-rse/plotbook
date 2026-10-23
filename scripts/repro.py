"""Reproduce an old artifact from its commit in a temporary git worktree.

    plotbook-repro <artifact>      e.g. primary0003, primary0003-foo, plots/primary0003-foo.svg

Never touches the working tree: finds the commit that last committed the artifact (falling back
to the SVG's provenance metadata), adds a detached worktree there, symlinks the analysis dir's
untracked/ignored top-level entries (data files usually live outside git) into it, re-runs the
script from the worktree's analysis root, and compares outputs with the committed ones.
"""

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402

METADATA_RE = re.compile(r"<metadata>.*?</metadata>", re.S)


def resolve(root, arg):
    name = os.path.basename(arg)
    m = re.match(r"^([A-Za-z](?:[A-Za-z_-]*[A-Za-z])?)(\d{4})", name)
    if not m:
        raise pbcore.PlotbookError("not an artifact name: %r" % arg)
    thread, n = m.group(1), int(m.group(2))
    scripts = [s for k, s in pbcore.artifacts(root, thread, "scripts") if k == n and s.endswith(".py")]
    outputs = [o for k, o in pbcore.artifacts(root, thread, "plots") if k == n]
    if os.path.splitext(name)[0] != m.group(0):  # a specific output was named
        outputs = [o for o in outputs if o.startswith(os.path.splitext(name)[0])] or outputs
    if len(scripts) != 1:
        raise pbcore.PlotbookError("expected one script for %s%04d, found %s" % (thread, n, scripts or "none"))
    return scripts[0], outputs


def provenance_commit(path):
    try:
        text = open(path).read(20000)
    except OSError:
        return None
    m = re.search(r"<dc:description>(.*?)</dc:description>", text, re.S)
    if not m:
        return None
    try:
        info = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))
    except ValueError:
        return None
    c = info.get("commit")
    return c if c and c != "unknown" else None


def normalized(data):
    try:
        return METADATA_RE.sub("", data.decode()).splitlines()
    except UnicodeDecodeError:
        return None


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    m = re.match(r"^([A-Za-z](?:[A-Za-z_-]*[A-Za-z])?)\d{4}", os.path.basename(argv[0]))
    try:
        root = pbcore.tool_root(m.group(1) if m else None)
    except pbcore.PlotbookError as err:
        sys.exit("plotbook-repro: %s" % err)
    try:
        script, outputs = resolve(root, argv[0])
    except pbcore.PlotbookError as err:
        sys.exit("plotbook-repro: %s" % err)
    target = os.path.join("plots", outputs[0]) if outputs else os.path.join("scripts", script)
    sha = pbcore.git(["log", "-1", "--format=%H", "--", target], root, check=False).stdout.strip()
    how = "last commit of %s" % target
    if not sha and outputs:
        sha, how = provenance_commit(os.path.join(root, target)), "SVG provenance metadata"
    if not sha:
        sys.exit("plotbook-repro: %s has never been committed and has no provenance commit" % target)
    top = pbcore.git_out(["rev-parse", "--show-toplevel"], root)
    prefix = os.path.relpath(os.path.realpath(root), os.path.realpath(top))
    tmp = tempfile.mkdtemp(prefix="plotbook-repro-")
    wt = os.path.join(tmp, "worktree")
    outdir = pbcore.ensure_dir(os.path.join(tmp, "outputs"))
    print("artifact: scripts/%s  commit: %s (%s)" % (script, sha[:12], how))
    with pbcore.repo_lock(root):
        pbcore.git(["worktree", "add", "--detach", wt, sha], root)
    try:
        wroot = os.path.normpath(os.path.join(wt, prefix))
        committed = {}
        for o in outputs:
            cp = subprocess.run(["git", "show", "%s:./%s" % (sha, os.path.join("plots", o))], cwd=root, capture_output=True)
            if cp.returncode == 0:
                committed[o] = cp.stdout
        linked = []
        for entry in sorted(os.listdir(root)):
            if entry in ("plots", "scripts", ".plotbook") or os.path.lexists(os.path.join(wroot, entry)):
                continue
            os.symlink(os.path.join(root, entry), os.path.join(wroot, entry))
            linked.append(entry)
        if linked:
            print("linked untracked entries into the worktree: %s" % ", ".join(linked))
        print("running: python scripts/%s   (in %s)" % (script, wroot))
        cp = subprocess.run([sys.executable, os.path.join("scripts", script)], cwd=wroot, capture_output=True, text=True,
                            timeout=3600)
        if cp.returncode != 0:
            print("FAILED (exit %d):\n%s" % (cp.returncode, (cp.stderr or cp.stdout)[-3000:]))
            return 1
        for o in outputs:
            produced = os.path.join(wroot, "plots", o)
            if not os.path.exists(produced):
                print("%s: not produced by the script at this commit" % o)
                continue
            dest = os.path.join(outdir, o)
            shutil.copyfile(produced, dest)
            new = open(produced, "rb").read()
            old = committed.get(o)
            if old is None:
                verdict = "no committed version to compare"
            elif new == old:
                verdict = "identical"
            else:
                a, b = normalized(old), normalized(new)
                if a is not None and a == b:
                    verdict = "identical apart from metadata"
                elif a is not None and b is not None:
                    d = [l for l in difflib.unified_diff(a, b, lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
                    verdict = "DIFFERS: %d changed lines (ignoring metadata)" % len(d)
                else:
                    verdict = "DIFFERS (binary)"
            print("%s: %s\n  reproduced output: %s" % (o, verdict, dest))
    finally:
        with pbcore.repo_lock(root):
            pbcore.git(["worktree", "remove", "--force", wt], root, check=False)
            pbcore.git(["worktree", "prune"], root, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
