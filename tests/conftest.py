import json
import os
import subprocess
import sys
import uuid

import pytest

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(PLUGIN, "scripts")
sys.path.insert(0, SCRIPTS)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """A git repo whose `analysis/` subdirectory will host a plotbook analysis."""
    r = tmp_path / "repo"
    (r / "analysis").mkdir(parents=True)
    git(r, "init", "-q", "-b", "analysis-branch")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "Test")
    git(r, "config", "commit.gpgsign", "false")
    (r / "README.md").write_text("repo\n")
    git(r, "add", "README.md")
    git(r, "commit", "-q", "-m", "init")
    return r


class Session:
    """Simulates one Claude Code session: hook calls plus a fake source transcript."""

    def __init__(self, root, tmp_path):
        self.root = str(root)
        self.id = str(uuid.uuid4())
        self.transcript = str(tmp_path / ("%s.jsonl" % self.id))
        open(self.transcript, "w").close()

    def hook(self, script, **extra):
        payload = {"session_id": self.id, "transcript_path": self.transcript, "cwd": self.root}
        payload.update(extra)
        env = dict(os.environ)
        env.pop("PLOTBOOK_HEADLESS", None)
        return subprocess.run([os.path.join(SCRIPTS, "guard.sh"), script], input=json.dumps(payload),
                              cwd=self.root, capture_output=True, text=True, env=env)

    def say(self, role, text, **kw):
        entry = {"type": role, "timestamp": "2026-10-21T00:00:00Z", "sessionId": self.id,
                 "message": {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]}}
        entry.update(kw)
        self.append(entry)

    def tool(self, name, **inp):
        self.append({"type": "assistant", "timestamp": "2026-10-21T00:00:01Z",
                     "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": name, "input": inp}]}})

    def append(self, entry):
        with open(self.transcript, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def prompt(self, text):
        self.say("user", text)
        return self.hook("turn_start.py", prompt=text, hook_event_name="UserPromptSubmit")

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        r = self.hook("immutability_guard.py", tool_name="Write", tool_input={"file_path": path, "content": content})
        if r.stdout.strip():
            return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        with open(path, "w") as f:
            f.write(content)
        self.tool("Write", file_path=path, content=content)
        self.hook("journal.py", tool_name="Write", tool_input={"file_path": path, "content": content})
        return None

    def run(self, *cmd):
        self.tool("Bash", command=" ".join(cmd))
        return subprocess.run(list(cmd), cwd=self.root, capture_output=True, text=True,
                              env=dict(os.environ, PATH=os.path.join(PLUGIN, "bin") + os.pathsep + os.environ["PATH"]))

    def stop(self, reply="done"):
        self.say("assistant", reply)
        return self.hook("turn_commit.py", hook_event_name="Stop", last_assistant_message=reply)


@pytest.fixture
def new_session(repo, tmp_path, monkeypatch):
    # No viewer during tests: make `uv` unfindable for the start hook.
    monkeypatch.setenv("PATH", ":".join(p for p in os.environ["PATH"].split(":") if not os.path.exists(os.path.join(p, "uv"))))

    def make():
        return Session(repo / "analysis", tmp_path)

    return make


def commits(repo):
    """[(subject, [files])] newest first."""
    out = []
    log = git(repo, "log", "--format=%x00%s", "--name-only")
    for chunk in log.split("\0")[1:]:
        lines = [l for l in chunk.splitlines() if l.strip()]
        out.append((lines[0], lines[1:]))
    return out
