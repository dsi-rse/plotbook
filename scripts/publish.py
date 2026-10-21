"""Publish a thread's draft as a PR comment. The ONLY plotbook code that pushes or comments.

    publish.py --root <analysis> --thread T [--plan]

Run by the viewer when the user presses Publish; deterministic (no model call). Logs one
JSON object per line: {"step", "msg"} ... then {"done": true, "url"} or {"error"}.
Resumable: progress is kept in .plotbook/state/publish-T.json and the composed body is
saved before any network step, so a failure never loses the comment.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pbcore  # noqa: E402

IMAGE_EXTS = {"svg", "png", "jpg", "jpeg", "gif", "webp"}
SUMMARY = "🤖 Extra details 🤖"
# Local artifact references the draft view inserts (and the obvious markdown forms).
IMG_TAG_RE = re.compile(r"<img\b[^>]*?\bsrc=\"(plots/[^\"]+)\"[^>]*>", re.I)
MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\((plots/[^)\s]+)\)")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\((plots/[^)\s]+)\)")
HREF_RE = re.compile(r"\bhref=\"(plots/[^\"]+)\"", re.I)


class PublishError(Exception):
    pass


def emit(rec):
    """One JSON log line for the viewer. If the viewer has gone away, carry on silently."""
    try:
        print(json.dumps(rec), flush=True)
    except (BrokenPipeError, ValueError):
        devnull = open(os.devnull, "w")
        sys.stdout = sys.stderr = devnull


def log(step, msg):
    emit({"step": step, "msg": msg})


def run(args, cwd, timeout=120):
    cp = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if cp.returncode != 0:
        raise PublishError("%s failed:\n%s" % (" ".join(args[:3]), (cp.stderr or cp.stdout).strip()))
    return cp.stdout


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


# ---------------------------------------------------------------- environment


def pr_info(root):
    try:
        out = run(["gh", "pr", "view", "--json", "number,url,headRefName,headRepository,headRepositoryOwner"], root, 60)
    except PublishError as err:
        raise PublishError("no GitHub PR found for the current branch. plotbook never creates PRs: push the branch "
                           "and open a PR for it (e.g. `gh pr create --draft`), then publish again.\n(%s)" % err)
    d = json.loads(out)
    repo = "%s/%s" % (d["headRepositoryOwner"]["login"], d["headRepository"]["name"])
    return {"number": d["number"], "url": d["url"], "repo": repo, "head": d["headRefName"]}


def visibility(root, repo):
    try:
        return json.loads(run(["gh", "repo", "view", repo, "--json", "visibility"], root, 60))["visibility"].lower()
    except (PublishError, ValueError, KeyError):
        return "private"  # the form that works for both


def branch_and_remote(root):
    branch = pbcore.git_out(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch == "HEAD":
        raise PublishError("detached HEAD; check out the analysis branch first")
    remote = pbcore.git(["config", "branch.%s.remote" % branch], root, check=False).stdout.strip() or "origin"
    merge = pbcore.git(["config", "branch.%s.merge" % branch], root, check=False).stdout.strip()
    dest = merge[len("refs/heads/"):] if merge.startswith("refs/heads/") else branch
    return branch, remote, dest


FONTS_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<!-- plotbook: render previews with matplotlib's bundled fonts only (the files it laid the text out with) -->
<fontconfig>
  <dir>%(fonts)s</dir>
  <cachedir>%(cache)s</cachedir>
  <alias><family>sans-serif</family><prefer><family>DejaVu Sans</family></prefer></alias>
  <alias><family>serif</family><prefer><family>DejaVu Serif</family></prefer></alias>
  <alias><family>monospace</family><prefer><family>DejaVu Sans Mono</family></prefer></alias>
  <!-- any other family (e.g. Helvetica) falls back to DejaVu Sans, not matplotlib's box-drawing LastResort -->
  <match target="pattern"><edit name="family" mode="append_last"><string>DejaVu Sans</string></edit></match>
</fontconfig>
"""


def isolate_fonts(root):
    """Make every SVG→PNG converter resolve fonts from matplotlib's bundled set, not the system's.

    System font setups differ (and can be broken: a conda fontconfig that misreads .woff files
    resolved "DejaVu Sans" to an italic Garamond). Must run before cairosvg/cairo is loaded;
    rsvg-convert and Inkscape inherit it through FONTCONFIG_FILE. Returns the fonts dir or None.
    """
    from xml.sax.saxutils import escape

    data = None
    for py in ("python3", "python"):
        exe = shutil.which(py)
        if exe:
            cp = subprocess.run([exe, "-c", "import matplotlib; print(matplotlib.get_data_path())"],
                                capture_output=True, text=True, timeout=60)
            if cp.returncode == 0 and cp.stdout.strip():
                data = cp.stdout.strip()
                break
    fonts = os.path.join(data, "fonts", "ttf") if data else None
    if not fonts or not os.path.isdir(fonts):
        return None
    conf_dir = pbcore.ensure_dir(pbcore.state_dir(root, "fontconfig"))
    conf = os.path.join(conf_dir, "fonts.conf")
    with open(conf, "w") as f:
        f.write(FONTS_CONF % {"fonts": escape(fonts), "cache": escape(os.path.join(conf_dir, "cache"))})
    os.environ["FONTCONFIG_FILE"] = conf
    return fonts


def converter():
    try:
        import cairosvg  # noqa: F401

        return "cairosvg"
    except Exception:  # ImportError, or OSError when libcairo is missing
        pass
    if shutil.which("rsvg-convert"):
        return "rsvg-convert"
    if shutil.which("inkscape"):
        return "inkscape"
    return None


def svg_size(path):
    """(width, height) in CSS px from the root <svg> element, or None."""
    head = read(path)[:4000]
    m = re.search(r"<svg\b[^>]*>", head, re.S)
    if not m:
        return None
    units = {"pt": 4 / 3, "px": 1, "": 1, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4}
    dims = []
    for attr in ("width", "height"):
        a = re.search(r'\b%s="([\d.]+)([a-z]*)"' % attr, m.group(0))
        if not a or a.group(2) not in units:
            return None
        dims.append(float(a.group(1)) * units[a.group(2)])
    return tuple(dims)


def render_png(svg, png, conv):
    size = svg_size(svg)
    width = int(min(1600, 2 * size[0])) if size else 1600
    if conv == "cairosvg":
        import cairosvg

        cairosvg.svg2png(url=svg, write_to=png, output_width=width, background_color="white")
    elif conv == "rsvg-convert":
        subprocess.run(["rsvg-convert", "-w", str(width), "-b", "white", "-o", png, svg], check=True, capture_output=True)
    else:
        subprocess.run(["inkscape", svg, "--export-type=png", "--export-filename=" + png, "--export-width=%d" % width,
                        "--export-background=white", "--export-background-opacity=1"], check=True, capture_output=True)


# ---------------------------------------------------------------- draft parsing / rewriting


def referenced(draft):
    """Ordered, de-duplicated analysis-relative paths of artifacts the draft refers to."""
    refs = []
    for rx, group in ((IMG_TAG_RE, 1), (MD_IMG_RE, 2), (MD_LINK_RE, 2), (HREF_RE, 1)):
        for m in rx.finditer(draft):
            p = urllib.parse.unquote(m.group(group))
            if p not in refs:
                refs.append(p)
    return sorted(refs, key=draft.find)


def preview_rel(rel):
    return os.path.join(".plotbook", "previews", os.path.splitext(os.path.basename(rel))[0] + ".png")


def needs_preview(rel):
    return rel.lower().endswith(".svg")


class Urls:
    def __init__(self, repo, sha, prefix, private):
        self.repo, self.sha, self.prefix, self.private = repo, sha, prefix, private

    def _path(self, rel):
        full = "/".join(p for p in (self.prefix, rel) if p and p != ".")
        return "/".join(urllib.parse.quote(seg) for seg in full.split("/"))

    def blob(self, rel):
        return "https://github.com/%s/blob/%s/%s" % (self.repo, self.sha, self._path(rel))

    def raw(self, rel):
        if self.private:
            return self.blob(rel) + "?raw=true"
        return "https://raw.githubusercontent.com/%s/%s/%s" % (self.repo, self.sha, self._path(rel))

    def image_src(self, rel):
        return self.raw(preview_rel(rel) if needs_preview(rel) else rel)


def rewrite(draft, urls):
    """Local plots/ references -> SHA-pinned URLs; images become previews linked to the file blob."""
    def img_tag(m):
        rel = urllib.parse.unquote(m.group(1))
        tag = m.group(0).replace('src="%s"' % m.group(1), 'src="%s"' % urls.image_src(rel), 1)
        return '<a href="%s">%s</a>' % (urls.blob(rel), tag)

    def md_img(m):
        rel = urllib.parse.unquote(m.group(2))
        alt = m.group(1) or os.path.basename(rel)
        return '<a href="%s"><img src="%s" alt="%s"></a>' % (urls.blob(rel), urls.image_src(rel), alt.replace('"', "&quot;"))

    out = IMG_TAG_RE.sub(img_tag, draft)
    out = MD_IMG_RE.sub(md_img, out)
    out = MD_LINK_RE.sub(lambda m: "[%s](%s)" % (m.group(1), urls.blob(urllib.parse.unquote(m.group(2)))), out)
    out = HREF_RE.sub(lambda m: 'href="%s"' % urls.blob(urllib.parse.unquote(m.group(1))), out)
    return out


def compose(body, addendum):
    body = body.rstrip() + "\n"
    if addendum.strip():
        body += "\n<details><summary>%s</summary>\n\n%s\n\n</details>\n" % (SUMMARY, addendum.strip())
    return body


# ---------------------------------------------------------------- pipeline


def paths(root, thread):
    return {"draft": pbcore.state_dir(root, "draft-%s.md" % thread),
            "addendum": pbcore.state_dir(root, "addendum-%s.md" % thread),
            "progress": pbcore.state_dir(root, "publish-%s.json" % thread),
            "body": pbcore.state_dir(root, "publish-%s-body.md" % thread)}


def pending_paths(root, thread):
    return pbcore.thread_pending(root, thread)


def plan(root, thread):
    p = paths(root, thread)
    draft, addendum = read(p["draft"]), read(p["addendum"])
    refs = referenced(draft)
    pr = pr_info(root)
    branch, remote, dest = branch_and_remote(root)
    warnings = []
    if not draft.strip():
        warnings.append("Your comment is empty.")
    if dest != pr["head"]:
        warnings.append("Branch %s pushes to %s, but the PR's head branch is %s." % (branch, dest, pr["head"]))
    return {"pr": pr, "visibility": visibility(root, pr["repo"]), "branch": branch, "remote": remote,
            "pending": pending_paths(root, thread),
            "images": [r for r in refs if r.rsplit(".", 1)[-1].lower() in IMAGE_EXTS],
            "links": [r for r in refs if r.rsplit(".", 1)[-1].lower() not in IMAGE_EXTS],
            "missing": [r for r in refs if not os.path.exists(os.path.join(root, r))],
            "converter": converter(), "addendum": bool(addendum.strip()), "warnings": warnings}


def push(root):
    branch, remote, dest = branch_and_remote(root)
    cp = pbcore.git(["push", remote, "HEAD:refs/heads/" + dest], root, check=False)
    if cp.returncode != 0:
        raise PublishError("git push %s HEAD:%s was rejected (plotbook never force-pushes). If the remote branch has "
                           "commits you don't have, pull them yourself, then publish again.\n%s"
                           % (remote, dest, cp.stderr.strip()))
    return "%s/%s" % (remote, dest)


def publish(root, thread):
    p = paths(root, thread)
    progress = pbcore.read_json(p["progress"]) or {}
    draft, addendum = read(p["draft"]), read(p["addendum"])
    if progress.get("comment_url") is None and not (draft.strip() or addendum.strip()):
        raise PublishError("nothing to publish: the comment and the Extra-details pane are both empty")

    log("pr", "resolving the PR for the current branch")
    pr = pr_info(root)
    private = visibility(root, pr["repo"]) != "public"
    log("pr", "PR #%d in %s (%s)" % (pr["number"], pr["repo"], "private" if private else "public"))

    if progress.get("comment_url") is None:
        refs = referenced(draft)
        missing = [r for r in refs if not os.path.exists(os.path.join(root, r))]
        if missing:
            raise PublishError("the draft refers to files that don't exist: %s" % ", ".join(missing))
        previews = []
        svgs = [r for r in refs if needs_preview(r)]
        if svgs:
            conv = converter()
            if conv is None:
                raise PublishError("no SVG→PNG converter found. Install one of: cairosvg (pip install cairosvg), "
                                   "rsvg-convert (librsvg), or Inkscape; then publish again.")
            for rel in svgs:
                src, dst = os.path.join(root, rel), os.path.join(root, preview_rel(rel))
                # Always re-render (cheap, deterministic): a preview from an older renderer or font
                # setup must not be reused; unchanged output makes no diff.
                pbcore.ensure_dir(os.path.dirname(dst))
                render_png(src, dst, conv)
                log("previews", "rendered %s (%s)" % (preview_rel(rel), conv))
                previews.append(preview_rel(rel))

        # One commit per publish: the thread's pending work (files, transcripts, the previous
        # comment's manifest entry) plus the PNG previews. The comment's links pin this commit.
        todo = pbcore.thread_pending(root, thread) + previews
        n = len(previews)
        what = ("previews for comment (%d image%s)" % (n, "" if n == 1 else "s")) if n else "snapshot for comment"
        body = "\n".join(["Previews:"] + ["- " + os.path.basename(r) for r in previews]) + "\n\n" if n else ""
        sha = pbcore.commit_paths(root, todo, "%s %s" % (pbcore.artifact_range(todo, thread), what),
                                  body + "thread: %s" % thread)
        log("commit", "committed %s" % sha[:10] if sha else "nothing new to commit")

        log("push", "pushing")
        where = push(root)
        head = pbcore.head_sha(root)
        log("push", "pushed %s to %s" % (head[:10], where))

        top = pbcore.git_out(["rev-parse", "--show-toplevel"], root)
        prefix = os.path.relpath(os.path.realpath(root), os.path.realpath(top)).replace(os.sep, "/")
        urls = Urls(pr["repo"], head, "" if prefix == "." else prefix, private)
        body = compose(rewrite(draft, urls), addendum)
        with open(p["body"], "w") as f:
            f.write(body)
        progress = {"head_sha": head, "artifacts": refs, "body": p["body"], "pr": pr, "started": pbcore.now_iso()}
        pbcore.write_json(p["progress"], progress)
        log("compose", "comment body saved to %s" % os.path.relpath(p["body"], root))

        log("comment", "posting the comment")
        out = run(["gh", "pr", "comment", pr["url"], "--body-file", p["body"]], root, 120)
        url = next((w for w in out.split() if w.startswith("https://")), out.strip())
        progress["comment_url"] = url
        pbcore.write_json(p["progress"], progress)
        log("comment", "posted %s" % url)
    else:
        log("resume", "comment already posted (%s); finishing the manifest" % progress["comment_url"])

    through = max([n for n, _ in pbcore.artifacts(root, thread, "plots")] +
                  [n for n, _ in pbcore.artifacts(root, thread, "scripts")], default=0)
    rec = {"ts": pbcore.now_iso(), "thread": thread, "artifacts": progress["artifacts"], "through": through,
           "head_sha": progress["head_sha"], "comment_url": progress["comment_url"],
           "pr": progress["pr"]["number"], "pr_url": progress["pr"]["url"]}
    manifest = os.path.join(root, pbcore.MANIFEST)
    if not any(r.get("comment_url") == rec["comment_url"] for r in pbcore.published(root)):
        pbcore.append_jsonl(manifest, rec)
    # Not committed now (that would be a commit just for the comment URL): the entry rides along in
    # this thread's next commit. The viewer's divider reads the working-tree file, so it moves now.
    log("manifest", "recorded in .plotbook/published.jsonl (committed with the next change)")

    archive = pbcore.ensure_dir(pbcore.state_dir(root, "archive"))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for kind in ("draft", "addendum", "body"):
        if os.path.exists(p[kind]):
            shutil.move(p[kind], os.path.join(archive, "%s-%s-%s.md" % (stamp, thread, kind)))
    for leftover in (p["progress"], pbcore.state_dir(root, "addendum-%s.meta.json" % thread)):
        if os.path.exists(leftover):
            os.remove(leftover)
    emit({"done": True, "url": rec["comment_url"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--thread", required=True)
    ap.add_argument("--plan", action="store_true", help="print what would happen, as JSON, and exit")
    args = ap.parse_args()
    root = pbcore.find_root(args.root)
    try:
        if root is None:
            raise PublishError("not a plotbook analysis: %s" % args.root)
        if isolate_fonts(root) is None and not args.plan:
            log("previews", "matplotlib not found; previews use the system's fonts")
        if args.plan:
            print(json.dumps(plan(root, args.thread)))
        else:
            publish(root, args.thread)
    except (PublishError, pbcore.PlotbookError, subprocess.SubprocessError, OSError) as err:
        if args.plan:
            print(json.dumps({"error": str(err)}))
        else:
            emit({"error": str(err)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
