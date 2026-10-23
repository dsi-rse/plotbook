import json
import os
import subprocess
import sys
import threading

from conftest import commits, git

import pbcore

PLOT = ('"""{name}"""\nimport os\n'
        'os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n'
        'exec(compile(open(_f := "scripts/shared_{leaf:04d}.py").read(), _f, "exec"))\n'
        'fig, ax = plt.subplots()\nax.plot([1, 2, {k}])\nsave(fig)\n')


def make_plot(s, thread, n, slug="demo", leaf=0, k=3):
    rel = "scripts/%s%04d-%s.py" % (thread, n, slug)
    assert s.write(rel, PLOT.format(name=rel, leaf=leaf, k=k)) is None
    r = s.run("python3", os.path.relpath(os.path.join(s.root, rel), s.cwd))  # from wherever the session runs
    assert r.returncode == 0, r.stderr
    return rel


def test_start_plot_commit(new_session, repo):
    s = new_session()
    r = s.prompt("/plotbook:start primary")
    assert r.returncode == 0, r.stderr
    assert 'owns thread "primary"' in r.stdout
    assert "next=primary0001" in r.stdout and "leaf=shared_0000" in r.stdout
    assert sorted(os.listdir(s.root)) == [".plotbook", "plots", "scripts"]
    assert s.stop("Claimed.").returncode == 0
    subject, files = commits(repo)[0]
    assert subject.startswith("[primary] ")
    assert sorted(files) == sorted(["analysis/.plotbook/.gitignore", "analysis/scripts/shared_0000.py",
                                    "analysis/.plotbook/transcripts/primary-%s.jsonl" % s.id[:8]])

    r = s.prompt("plot the penguin masses please")
    assert "next=primary0001" in r.stdout
    make_plot(s, "primary", 1)
    assert s.run("plotbook-note", "primary", "plot penguin masses").returncode == 0
    assert s.stop("Here it is.").returncode == 0
    subject, files = commits(repo)[0]
    assert subject == "[primary0001] plot penguin masses"
    assert sorted(files) == sorted(["analysis/plots/primary0001-demo.svg", "analysis/scripts/primary0001-demo.py",
                                    "analysis/.plotbook/transcripts/primary-%s.jsonl" % s.id[:8]])
    assert sorted(os.listdir(s.root)) == [".plotbook", "plots", "scripts"]
    assert git(repo, "status", "--porcelain") == ""
    body = git(repo, "log", "-1", "--format=%b")
    assert "plot the penguin masses please" in body and s.id in body

    # The filtered transcript: dialogue + anchors, no tool results.
    lines = [json.loads(l) for l in open(os.path.join(s.root, ".plotbook/transcripts/primary-%s.jsonl" % s.id[:8]))]
    assert lines[0] == {"plotbook_transcript": 1, "session": s.id, "thread": "primary"}
    assert {"event": "wrote", "ts": "2026-10-21T00:00:01Z", "file": "scripts/primary0001-demo.py"} in lines
    assert any(l.get("event") == "ran" and l["command"] == "python3 scripts/primary0001-demo.py" for l in lines)
    assert lines[-1]["role"] == "assistant" and lines[-1]["text"] == "Here it is."

    # Next turn: fallback subject when no note; range across artifacts.
    s.prompt("make two variants with the third point higher, showing how the trend changes when it moves")
    make_plot(s, "primary", 2, k=5)
    make_plot(s, "primary", 3, k=7)
    s.stop()
    subject, _ = commits(repo)[0]
    assert subject.startswith("[primary0002–primary0003] make two variants")
    assert subject.endswith("…")


def test_unclaimed_session_and_outside_analysis(new_session, repo, tmp_path):
    s = new_session()
    r = s.prompt("hello")
    assert r.returncode == 0 and r.stdout == ""  # no .plotbook yet: guard exits silently
    s.prompt("/plotbook:start primary")
    s.stop()
    other = new_session()
    r = other.prompt("hi")
    assert "has not claimed a thread" in r.stdout
    n = len(commits(repo))
    other.stop()
    assert len(commits(repo)) == n


def test_duplicate_claim_refused_and_steal(new_session):
    a, b = new_session(), new_session()
    assert a.prompt("/plotbook:start primary").returncode == 0
    r = b.prompt("/plotbook:start primary")
    assert r.returncode == 2 and "already claimed" in r.stderr
    assert b.prompt("/plotbook:start shared").returncode == 2
    assert b.prompt("/plotbook:start bad1").returncode == 2
    assert b.prompt("/plotbook:start primary --steal").returncode == 0
    assert pbcore.session_thread(a.root, b.id) == "primary"
    assert pbcore.session_thread(a.root, a.id) is None


def test_concurrent_sessions_scoped_commits(new_session, repo):
    p, t = new_session(), new_session()
    p.prompt("/plotbook:start primary")
    p.stop()
    t.prompt("/plotbook:start tangent")
    t.stop()
    # Both allocate a shared delta in the same turn, then plot with it.
    p.prompt("add a helper")
    t.prompt("add a different helper")
    rp = p.run("plotbook-delta", "--thread", "primary", "--parent", "0000")
    rt = t.run("plotbook-delta", "--thread", "tangent", "--parent", "0000")
    dp, dt = rp.stdout.split()[0], rt.stdout.split()[0]
    assert dp != dt
    for s, d, val in ((p, dp, 10), (t, dt, 20)):
        txt = open(os.path.join(s.root, d)).read() + "HELPER = %d\n" % val
        assert s.write(d, txt) is None
    make_plot(p, "primary", 1, leaf=int(dp[15:19]))
    make_plot(t, "tangent", 1, leaf=int(dt[15:19]))
    results = {}
    th = [threading.Thread(target=lambda s=s: results.setdefault(s.id, s.stop())) for s in (p, t)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert all(r.returncode == 0 for r in results.values()), [r.stderr for r in results.values()]
    by_subject = {c[0].split()[0]: c[1] for c in commits(repo)[:2]}
    assert sorted(by_subject["[primary0001]"]) == sorted([
        "analysis/" + dp, "analysis/plots/primary0001-demo.svg", "analysis/scripts/primary0001-demo.py",
        "analysis/.plotbook/transcripts/primary-%s.jsonl" % p.id[:8]])
    assert sorted(by_subject["[tangent0001]"]) == sorted([
        "analysis/" + dt, "analysis/plots/tangent0001-demo.svg", "analysis/scripts/tangent0001-demo.py",
        "analysis/.plotbook/transcripts/tangent-%s.jsonl" % t.id[:8]])
    assert git(repo, "status", "--porcelain") == ""


def test_user_staged_files_untouched(new_session, repo):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop()
    (repo / "README.md").write_text("user edit\n")
    git(repo, "add", "README.md")
    s.prompt("plot")
    make_plot(s, "primary", 1)
    s.stop()
    _, files = commits(repo)[0]
    assert "README.md" not in files
    assert git(repo, "diff", "--cached", "--name-only").split() == ["README.md"]


def test_unknown_entry_type_is_loud_but_files_commit(new_session, repo):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop()
    s.prompt("plot")
    make_plot(s, "primary", 1)
    s.append({"type": "brand-new-entry-type", "sessionId": s.id})
    r = s.stop()
    assert r.returncode == 1
    assert "TRANSCRIPT-SYNC-BROKEN" in r.stderr and "brand-new-entry-type" in r.stderr
    marker = os.path.join(s.root, "TRANSCRIPT-SYNC-BROKEN")
    assert os.path.exists(marker) and "brand-new-entry-type" in open(marker).read()
    subject, files = commits(repo)[0]
    assert "analysis/plots/primary0001-demo.svg" in files
    assert "analysis/TRANSCRIPT-SYNC-BROKEN" not in files


def test_unknown_block_type_is_loud(new_session):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.append({"type": "assistant", "message": {"content": [{"type": "hologram"}]}})
    r = s.stop()
    assert r.returncode == 1 and "hologram" in r.stderr


def test_transcript_filter_drops_noise(new_session):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.append({"type": "attachment", "attachment": {"userEmail": "secret@example.com"}})
    s.say("user", "skill body", isMeta=True)
    s.append({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm", "signature": "x"}]}})
    s.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "big output"}]}})
    s.say("user", "<command-message>plotbook:push</command-message>\n<command-name>/plotbook:push</command-name>")
    s.stop("ok")
    text = open(os.path.join(s.root, pbcore.transcript_relpath("primary", s.id))).read()
    for bad in ("secret@example.com", "skill body", "hmm", "big output"):
        assert bad not in text
    assert '"text": "/plotbook:push"' in text


def test_transcript_regeneration_is_append_only(new_session, repo):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop("one")
    path = os.path.join(s.root, pbcore.transcript_relpath("primary", s.id))
    before = open(path).read()
    s.prompt("again")
    s.stop("two")
    after = open(path).read()
    assert after.startswith(before) and len(after) > len(before)


def test_immutability_guard(new_session, repo):
    p, t = new_session(), new_session()
    p.prompt("/plotbook:start primary")
    t.prompt("/plotbook:start tangent")
    p.stop()
    assert "managed by plotbook" in p.write(".plotbook/published.jsonl", "{}\n")
    assert "managed by plotbook" in p.write(pbcore.transcript_relpath("primary", p.id), "x")
    assert "append-only" in p.write("scripts/shared_0000.py", "# edited\n")
    assert "must be allocated" in p.write("scripts/shared_0009.py", "x = 1\n")
    assert "single-writer" in p.write("scripts/tangent0001-x.py", "print(1)\n")
    assert "exec line must be exactly" in p.write("scripts/primary0001-x.py", "exec(open('scripts/shared_0000.py').read())\n")
    d = p.run("plotbook-delta", "--thread", "primary", "--parent", "0").stdout.split()[0]
    assert "first line" in p.write(d, "x = 1\n")
    assert "belongs to thread primary" in t.write(d, open(os.path.join(p.root, d)).read() + "y = 2\n")
    assert p.write(d, open(os.path.join(p.root, d)).read() + "y = 2\n") is None
    # Published artifacts are immutable.
    make_plot(p, "primary", 1)
    with open(os.path.join(p.root, ".plotbook", "published.jsonl"), "w") as f:
        f.write(json.dumps({"thread": "primary", "artifacts": ["plots/primary0001-demo.svg"], "through": 1}) + "\n")
    assert "immutable" in p.write("scripts/primary0001-demo.py", "print(2)\n")
    assert p.write("scripts/primary0002-demo.py", "print(2)\n") is None


def test_conversation_only_turns_fold_into_next_commit(new_session, repo):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop()
    n = len(commits(repo))
    s.prompt("what does the bimodality mean?")
    assert s.stop("Probably two populations.").returncode == 0
    s.say("user", '! pkill -f "server/viewer.py --root"')  # `!` commands don't fire UserPromptSubmit
    assert s.stop("The viewer is stopped.").returncode == 0
    assert len(commits(repo)) == n  # no commits for turns that changed no files
    transcript = pbcore.transcript_relpath("primary", s.id)
    assert "Probably two populations." in open(os.path.join(s.root, transcript)).read()  # synced, not committed
    s.prompt("now plot it")
    make_plot(s, "primary", 1)
    s.stop()
    subject, files = commits(repo)[0]
    assert len(commits(repo)) == n + 1 and subject == "[primary0001] now plot it"
    assert "analysis/" + transcript in files
    body = git(repo, "log", "-1", "--format=%b")
    assert "what does the bimodality mean?" in body and "now plot it" in body
    assert "The viewer is stopped." in git(repo, "show", "HEAD:analysis/" + transcript)


def test_subject_falls_back_to_transcript_when_no_prompt_recorded(new_session, repo):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop()
    s.say("user", "! python3 scripts/primary0001-demo.py")  # typed by the user, no UserPromptSubmit
    rel = "scripts/primary0001-demo.py"
    with open(os.path.join(s.root, rel), "w") as f:
        f.write("print(1)\n")
    s.stop()
    assert commits(repo)[0][0] == "[primary0001] ! python3 scripts/primary0001-demo.py"


def test_backstop_sessionend_releases(new_session, repo):
    s = new_session()
    s.prompt("/plotbook:start primary")
    s.stop()
    n = len(commits(repo))
    s.say("user", "one more thing")
    r = s.hook("sync_transcript.py", hook_event_name="SessionEnd")
    assert r.returncode == 0, r.stderr
    assert len(commits(repo)) == n  # sync only; the next commit carries it
    assert "one more thing" in open(os.path.join(s.root, pbcore.transcript_relpath("primary", s.id))).read()
    assert pbcore.session_thread(s.root, s.id) is None
    # Resuming the same session re-claims its thread.
    r = s.prompt("resumed")
    assert "thread=primary" in r.stdout


def _fake_viewer(root):
    """A process that looks like `uv run ... viewer.py --root <root>` to claim_thread."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "viewer.py", "--root",
                             os.path.realpath(root)])


def test_sessionend_stops_viewer_after_last_live_session(new_session, tmp_path):
    a, b = new_session(), new_session()
    a.prompt("/plotbook:start primary")
    b.prompt("/plotbook:start tangent")
    viewer = _fake_viewer(a.root)
    other = _fake_viewer(tmp_path)  # another analysis's viewer: never touched
    try:
        pbcore.write_json(pbcore.state_dir(a.root, "viewer.json"), {"pid": viewer.pid, "port": 1})
        assert a.hook("sync_transcript.py", hook_event_name="SessionEnd").returncode == 0
        assert viewer.poll() is None  # tangent's session is still live
        assert b.hook("sync_transcript.py", hook_event_name="SessionEnd").returncode == 0
        assert viewer.wait(timeout=5) != 0 and other.poll() is None
    finally:
        for p in (viewer, other):
            p.kill()
            p.wait()
    # Resuming brings the viewer back (here: reports why it can't, since tests hide `uv`).
    r = a.prompt("resumed")
    assert "thread=primary" in r.stdout and "viewer not started" in r.stdout
    assert "viewer not started" not in a.prompt("again").stdout


def test_repo_root_session_with_analysis_dir(new_session, repo):
    s = new_session(root=repo / "nb", cwd=repo)
    r = s.prompt("/plotbook:start A nb")
    assert r.returncode == 0, r.stderr
    nb = os.path.realpath(repo / "nb")
    assert "dir=%s " % nb in r.stdout and os.path.isdir(os.path.join(nb, ".plotbook"))
    reg = json.load(open(repo / ".git" / "plotbook" / "sessions" / (s.id + ".json")))
    assert reg["root"] == nb and reg["thread"] == "A"
    s.stop()
    assert "nb/scripts/shared_0000.py" in commits(repo)[0][1]
    # A turn from the repo root: guard, journal, a script run from the repo root, the note, the commit.
    assert "dir=%s " % nb in s.prompt("first plot").stdout
    make_plot(s, "A", 1)
    assert s.run("plotbook-note", "A", "histogram of masses").returncode == 0
    s.stop()
    subject, files = commits(repo)[0]
    assert subject == "[A0001] histogram of masses"
    assert sorted(f for f in files if not f.startswith("nb/.plotbook")) == ["nb/plots/A0001-demo.svg", "nb/scripts/A0001-demo.py"]
    assert "append-only" in s.write("scripts/shared_0000.py", "# edited\n")
    d = s.run("plotbook-delta", "--thread", "A", "--parent", "0").stdout.split()[0]
    assert d == "nb/scripts/shared_0001.py"  # relative to where it ran
    # Resume after SessionEnd finds the analysis again.
    assert s.hook("sync_transcript.py", hook_event_name="SessionEnd").returncode == 0
    assert "thread=A dir=%s " % nb in s.prompt("resumed").stdout
    # A new session (e.g. after /clear) gets thread A back in nb without naming the directory.
    assert s.hook("sync_transcript.py", hook_event_name="SessionEnd").returncode == 0
    s2 = new_session(root=repo / "nb", cwd=repo)
    r = s2.prompt("/plotbook:start A")
    assert r.returncode == 0 and "dir=%s " % nb in r.stdout, r.stderr
    # Sessions at the repo root that never claimed a thread are left alone.
    other = new_session(root=repo / "nb", cwd=repo)
    r = other.prompt("unrelated question")
    assert r.returncode == 0 and r.stdout == ""
    assert other.stop().returncode == 0 and commits(repo)[0][0] == subject


def test_start_argument_errors_and_moving_analyses(new_session, repo, tmp_path):
    s = new_session(root=repo / "nb", cwd=repo)
    for args, msg in [("", "usage"), ("A nb extra", "usage"), ("A --bogus", "usage"),
                      ("A ../elsewhere", "not inside a git repository")]:
        r = s.prompt(("/plotbook:start " + args).strip())
        assert r.returncode == 2 and msg in r.stderr, (args, r.stderr)
    assert not os.path.exists(tmp_path / "elsewhere")
    assert s.prompt("/plotbook:start A nb").returncode == 0
    r = s.prompt("/plotbook:start A nb/scripts")
    assert r.returncode == 2 and "can't nest" in r.stderr
    # Moving this session to another analysis releases its thread in the first one.
    r = s.prompt("/plotbook:start B other")
    assert r.returncode == 0 and 'Released this session\'s previous thread "A"' in r.stdout
    assert pbcore.session_thread(str(repo / "nb"), s.id) is None
    assert pbcore.session_thread(str(repo / "other"), s.id) == "B"
