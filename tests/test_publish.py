import json
import os
import subprocess
import sys

import pytest
from conftest import PLUGIN, SCRIPTS, commits, git
from test_hooks import make_plot

import pbcore
import publish

STUB_GH = r'''#!/usr/bin/env python3
import json, os, sys, shutil
a = sys.argv[1:]
log = os.environ["STUB_GH_LOG"]
open(log, "a").write(json.dumps(a) + "\n")
if a[:2] == ["pr", "view"]:
    print(json.dumps({"number": 7, "url": "https://github.com/o/r/pull/7", "headRefName": "analysis-branch",
                      "headRepository": {"name": "r"}, "headRepositoryOwner": {"login": "o"}}))
elif a[:2] == ["repo", "view"]:
    print(json.dumps({"visibility": os.environ.get("STUB_VIS", "PRIVATE")}))
elif a[:2] == ["pr", "comment"]:
    if os.environ.get("STUB_FAIL_COMMENT") and not os.path.exists(log + ".failed"):
        open(log + ".failed", "w").close(); sys.stderr.write("network down\n"); sys.exit(1)
    shutil.copyfile(a[a.index("--body-file") + 1], log + ".body")
    print("https://github.com/o/r/pull/7#issuecomment-%d" % (sum(1 for _ in open(log)) ))
else:
    sys.exit(1)
'''


@pytest.fixture
def published_env(new_session, repo, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "analysis-branch")
    stub = tmp_path / "stubbin"
    stub.mkdir()
    (stub / "gh").write_text(STUB_GH)
    (stub / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (stub, os.environ["PATH"]))
    monkeypatch.setenv("STUB_GH_LOG", str(tmp_path / "gh.log"))
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop()
    s.prompt("two plots")
    make_plot(s, "primary", 1)
    make_plot(s, "primary", 2, slug="map", k=4)
    with open(os.path.join(s.root, "plots", "primary0002-map.geojson"), "w") as f:
        f.write('{"type": "FeatureCollection", "features": []}\n')
    s.stop()
    make_plot(s, "primary", 3, slug="unmentioned")  # exists, but not referenced: still covered by "through"
    return s, remote, tmp_path


def write_state(s, draft, addendum):
    d = pbcore.state_dir(s.root)
    open(os.path.join(d, "draft-primary.md"), "w").write(draft)
    open(os.path.join(d, "addendum-primary.md"), "w").write(addendum)


def run_publish(s, *extra):
    cp = subprocess.run([sys.executable, os.path.join(SCRIPTS, "publish.py"), "--root", s.root, "--thread", "primary", *extra],
                        capture_output=True, text=True)
    return cp, [json.loads(l) for l in cp.stdout.splitlines() if l.strip()]


def test_publish_end_to_end(published_env, repo):
    s, remote, tmp = published_env
    draft = ("Masses look bimodal.\n\n<img src=\"plots/primary0001-demo.svg\" alt=\"primary0001-demo.svg\" width=\"600\">\n\n"
             "And the map: [primary0002-map.geojson](plots/primary0002-map.geojson)\n")
    write_state(s, draft, "- primary0001: histogram of `body_mass_g`, 30 bins.\n")
    cp, plan = run_publish(s, "--plan")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    plan = plan[0]
    assert plan["images"] == ["plots/primary0001-demo.svg"] and plan["links"] == ["plots/primary0002-map.geojson"]
    assert plan["converter"] and plan["visibility"] == "private" and plan["addendum"]
    assert "scripts/primary0003-unmentioned.py" in plan["pending"]

    cp, logs = run_publish(s)
    assert cp.returncode == 0 and logs[-1].get("done"), logs
    head = logs[-1]
    body = open(os.environ["STUB_GH_LOG"] + ".body").read()
    rec = pbcore.published(s.root)[-1]
    sha = rec["head_sha"]
    # SHA-pinned private-form preview, wrapped in a link to the SVG blob; filename visible in both URLs.
    assert ('<a href="https://github.com/o/r/blob/%s/analysis/plots/primary0001-demo.svg">'
            '<img src="https://github.com/o/r/blob/%s/analysis/.plotbook/previews/primary0001-demo.png?raw=true" '
            'alt="primary0001-demo.svg" width="600"></a>' % (sha, sha)) in body
    assert "[primary0002-map.geojson](https://github.com/o/r/blob/%s/analysis/plots/primary0002-map.geojson)" % sha in body
    assert body.startswith("Masses look bimodal.")
    assert "<details><summary>🤖 Extra details 🤖</summary>\n\n- primary0001: histogram of `body_mass_g`, 30 bins.\n\n</details>" in body
    # The pinned commit is on the remote and contains the preview.
    assert git(remote, "cat-file", "-t", sha).strip() == "commit"
    assert git(remote, "cat-file", "-t", "%s:analysis/.plotbook/previews/primary0001-demo.png" % sha).strip() == "blob"
    # Manifest: the divider covers everything existing (through = 3).
    assert rec["through"] == 3 and rec["artifacts"] == ["plots/primary0001-demo.svg", "plots/primary0002-map.geojson"]
    assert rec["comment_url"] == head["url"] and rec["pr"] == 7
    assert pbcore.last_through(s.root, "primary") == 3
    # One publish commit: the pending primary0003 work plus the preview. It is the pinned, pushed commit.
    subject, files = commits(repo)[0]
    assert subject == "[primary0001\u2013primary0003] previews for comment (1 image)"
    assert sorted(files) == sorted(["analysis/.plotbook/previews/primary0001-demo.png",
                                    "analysis/plots/primary0003-unmentioned.svg",
                                    "analysis/scripts/primary0003-unmentioned.py"])
    assert git(repo, "rev-parse", "HEAD").strip() == sha
    assert git(repo, "rev-parse", "HEAD") == git(remote, "rev-parse", "analysis-branch")
    # The manifest entry waits for the next commit (first publish: a new file).
    assert git(repo, "status", "--porcelain", "--", "analysis").split() == ["??", "analysis/.plotbook/published.jsonl"]
    # Draft archived and cleared.
    assert not os.path.exists(pbcore.state_dir(s.root, "draft-primary.md"))
    assert len(os.listdir(pbcore.state_dir(s.root, "archive"))) == 3


def test_empty_addendum_omits_details_and_manual_push_keeps_divider(published_env, repo):
    s, remote, tmp = published_env
    write_state(s, "First.\n", "")
    cp, logs = run_publish(s)
    assert logs[-1].get("done"), logs
    assert "<details>" not in open(os.environ["STUB_GH_LOG"] + ".body").read()
    through = pbcore.last_through(s.root, "primary")
    # More work, then a manual push: the divider derives only from published.jsonl.
    s.prompt("another")
    make_plot(s, "primary", 4)
    s.stop()
    git(repo, "push", "-q", "origin", "analysis-branch")
    assert pbcore.last_through(s.root, "primary") == through


def test_publish_resumes_after_comment_failure(published_env, monkeypatch):
    s, remote, tmp = published_env
    monkeypatch.setenv("STUB_FAIL_COMMENT", "1")
    write_state(s, "Hello <img src=\"plots/primary0001-demo.svg\" alt=\"x\">\n", "")
    cp, logs = run_publish(s)
    assert cp.returncode == 1 and "network down" in logs[-1]["error"]
    assert os.path.exists(pbcore.state_dir(s.root, "publish-primary-body.md"))  # nothing lost
    assert pbcore.published(s.root) == []
    cp, logs = run_publish(s)
    assert logs[-1].get("done"), logs
    assert len(pbcore.published(s.root)) == 1


def test_no_pr_is_a_hard_error(published_env, tmp_path):
    s, remote, tmp = published_env
    (tmp / "stubbin" / "gh").write_text("#!/bin/sh\necho 'no pull requests found' >&2\nexit 1\n")
    write_state(s, "x\n", "")
    cp, logs = run_publish(s)
    assert cp.returncode == 1 and "no GitHub PR found" in logs[-1]["error"]


def test_public_url_form():
    u = publish.Urls("o/r", "abc123", "an alysis", private=False)
    assert u.image_src("plots/a.svg") == "https://raw.githubusercontent.com/o/r/abc123/an%20alysis/.plotbook/previews/a.png"
    assert u.blob("plots/a.svg") == "https://github.com/o/r/blob/abc123/an%20alysis/plots/a.svg"
    assert publish.rewrite("![m](plots/a.svg)", u) == (
        '<a href="https://github.com/o/r/blob/abc123/an%20alysis/plots/a.svg">'
        '<img src="https://raw.githubusercontent.com/o/r/abc123/an%20alysis/.plotbook/previews/a.png" alt="m"></a>')


def test_repro_in_worktree_with_dirty_tree(new_session, repo):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop()
    s.prompt("plot")
    make_plot(s, "primary", 1)
    s.stop()
    # Uncommitted changes in the working tree, including to the very script being reproduced.
    rel = "scripts/primary0001-demo.py"
    with open(os.path.join(s.root, rel), "a") as f:
        f.write("# uncommitted edit\n")
    (repo / "analysis" / "data.csv").write_text("a,b\n")  # untracked data file gets linked
    before = git(repo, "status", "--porcelain")
    cp = subprocess.run([os.path.join(PLUGIN, "bin", "plotbook-repro"), "primary0001"], cwd=s.root,
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "primary0001-demo.svg: identical apart from metadata" in cp.stdout
    assert "linked untracked entries into the worktree: data.csv" in cp.stdout
    assert git(repo, "status", "--porcelain") == before
    assert git(repo, "worktree", "list").count("\n") == 1


def test_one_turn_then_publish_is_two_commits(published_env, repo):
    s, remote, tmp = published_env
    s.prompt("one more plot")
    make_plot(s, "primary", 4)
    s.stop()  # commit 1 (also carries primary0003, made without a Stop)
    n = len(commits(repo))
    write_state(s, '<img src="plots/primary0004-demo.svg" alt="primary0004-demo.svg">\n', "")
    cp, logs = run_publish(s)
    assert logs[-1].get("done"), logs
    assert len(commits(repo)) == n + 1  # commit 2: the previews; nothing else
    # A chat turn adds no commit; the next file-changing turn carries the manifest entry and the chat.
    s.prompt("thanks, what next?")
    s.stop("Maybe split by island.")
    assert len(commits(repo)) == n + 1
    s.prompt("split by island")
    make_plot(s, "primary", 5)
    s.stop()
    _, files = commits(repo)[0]
    assert "analysis/.plotbook/published.jsonl" in files
    assert git(repo, "status", "--porcelain", "--", "analysis") == ""
    # The next publish also carries a still-uncommitted manifest entry from the previous one.
    write_state(s, "text only\n", "")
    run_publish(s)
    write_state(s, "again\n", "")
    run_publish(s)
    _, files = commits(repo)[0]
    assert files == ["analysis/.plotbook/published.jsonl"] and len(pbcore.published(s.root)) == 3


def test_published_throughs_per_thread(tmp_path):
    root = tmp_path / "a"
    (root / ".plotbook").mkdir(parents=True)
    recs = [{"thread": "A", "through": 1}, {"thread": "B", "through": 7}, {"thread": "A", "through": 3},
            {"thread": "A", "artifacts": ["plots/A0005-x.svg", "plots/A0004-y.svg"]}]  # pre-`through` entry
    (root / ".plotbook" / "published.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert pbcore.published_throughs(str(root), "A") == [1, 3, 5]
    assert pbcore.last_through(str(root), "A") == 5 and pbcore.last_through(str(root), "C") == 0


def test_publish_finishes_when_viewer_goes_away(published_env):
    s, remote, tmp = published_env
    write_state(s, "Hello\n", "")
    proc = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "publish.py"), "--root", s.root, "--thread", "primary"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    proc.stdout.close()  # the reader (the viewer) is gone before the first log line
    assert proc.wait(timeout=60) == 0
    assert len(pbcore.published(s.root)) == 1
