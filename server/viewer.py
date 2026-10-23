# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "starlette>=0.40",
#   "uvicorn>=0.30",
#   "watchfiles>=0.24",
#   "cairosvg>=2.7",
#   "matplotlib>=3.5",  # only for its bundled fonts: publish renders PNG previews with them
#   "markdown-it-py>=3",
# ]
# ///
"""plotbook viewer: one localhost server per analysis, one browser tab per thread.

    uv run --script server/viewer.py --root <analysis-dir> [--port N]

Shows outputs only (plots/). Optional: every artifact is a plain file, browsable with no
server. Watches plots/ itself and pushes changes to pages over SSE.
"""

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.environ.get("PLOTBOOK_PLUGIN_ROOT") or os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "scripts"))
import pbcore  # noqa: E402

ROOT = None  # set in main()
IMAGE_EXTS = {"svg", "png", "jpg", "jpeg", "gif", "webp"}


# ---------------------------------------------------------------- state helpers


def thread_ok(thread):
    return pbcore.valid_thread(thread)


def artifact_list(thread):
    out = []
    for n, name in pbcore.artifacts(ROOT, thread, "plots"):
        p = os.path.join(ROOT, "plots", name)
        try:
            mtime = os.stat(p).st_mtime
        except OSError:
            continue
        ext = name.rsplit(".", 1)[-1].lower()
        out.append({"name": name, "n": n, "ext": ext, "image": ext in IMAGE_EXTS, "mtime": mtime})
    out.sort(key=lambda a: (a["n"], a["name"]))
    return out


_pr_cache = {"t": 0, "value": None}


def pr_info():
    """{url, number, repo} for the current branch's PR, cached for a minute; None if none."""
    if time.time() - _pr_cache["t"] < 60:
        return _pr_cache["value"]
    value = None
    try:
        cp = subprocess.run(["gh", "pr", "view", "--json", "url,number,headRepository,headRepositoryOwner"],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
        if cp.returncode == 0:
            d = json.loads(cp.stdout)
            value = {"url": d["url"], "number": d["number"],
                     "repo": "%s/%s" % (d["headRepositoryOwner"]["login"], d["headRepository"]["name"])}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass
    _pr_cache.update(t=time.time(), value=value)
    return value


def state_file(kind, thread):
    return pbcore.state_dir(ROOT, "%s-%s.md" % (kind, thread))


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def write_text(path, text):
    pbcore.ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def publish_basis(thread):
    """Identifies 'the last publish' for this thread; addendum auto-generation keys on it."""
    recs = [r for r in pbcore.published(ROOT) if r.get("thread") == thread]
    return "%d:%s" % (len(recs), recs[-1].get("comment_url", "") if recs else "")


def addendum_meta_path(thread):
    return pbcore.state_dir(ROOT, "addendum-%s.meta.json" % thread)


# ---------------------------------------------------------------- SSE broadcast


class Hub:
    def __init__(self):
        self.clients = set()

    def subscribe(self):
        q = asyncio.Queue()
        self.clients.add(q)
        return q

    def unsubscribe(self, q):
        self.clients.discard(q)

    def send(self, event):
        for q in list(self.clients):
            q.put_nowait(event)


HUB = Hub()
JOBS = {}  # (kind, thread) -> asyncio.Task


async def watch_files():
    from watchfiles import awatch

    pb = os.path.join(ROOT, ".plotbook")
    plots = os.path.join(ROOT, "plots")
    pbcore.ensure_dir(plots)
    async for changes in awatch(plots, pb, debounce=150, step=50,
                                watch_filter=lambda _c, p: p.startswith(plots) or p.endswith("published.jsonl")):
        if any(p.endswith("published.jsonl") for _, p in changes):
            HUB.send({"type": "published"})
        threads = sorted({(pbcore.parse_artifact(os.path.basename(p)) or [None])[0]
                          for _, p in changes if p.startswith(plots)} - {None})
        if threads:
            HUB.send({"type": "plots", "threads": threads})


async def events(request: Request):
    q = HUB.subscribe()

    async def stream():
        try:
            yield "retry: 1000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield "data: %s\n\n" % json.dumps(ev)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            HUB.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- routes


def head(title):
    return PAGE_HEAD.replace("__TITLE__", title)


async def health(request):
    return PlainTextResponse(os.path.realpath(ROOT))


async def index(request):
    threads = pbcore.threads_present(ROOT)
    items = "".join('<li><a href="/t/%s">%s</a> (%d outputs)</li>' % (t, t, len(artifact_list(t))) for t in threads)
    return HTMLResponse(head("plotbook") +
                        "<main class=index><h1>plotbook</h1><p class=path>%s</p><ul>%s</ul></main></body></html>"
                        % (ROOT, items or "<li>no threads yet; run /plotbook:start &lt;thread&gt;</li>"))


async def files(request):
    rel = os.path.normpath(request.path_params["path"])
    if not (rel.startswith("plots" + os.sep) or rel.startswith(os.path.join(".plotbook", "previews") + os.sep)):
        return Response(status_code=404)
    path = os.path.join(ROOT, rel)
    if not os.path.isfile(path):
        return Response(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


async def thread_page(request):
    t = request.path_params["thread"]
    if not thread_ok(t):
        return Response(status_code=404)
    return HTMLResponse(head(t) + THREAD_HTML.replace("__THREAD__", t) + SHARED_JS + THREAD_JS)


async def draft_page(request):
    t = request.path_params["thread"]
    if not thread_ok(t):
        return Response(status_code=404)
    return HTMLResponse(head(t + " draft") + DRAFT_HTML.replace("__THREAD__", t) + SHARED_JS + DRAFT_JS)


def earlier_throughs(thread):
    """Boundaries of the thread's earlier comments (grey dividers), excluding the latest one (red)."""
    throughs = pbcore.published_throughs(ROOT, thread)
    return sorted({b for b in throughs[:-1] if b > 0} - {throughs[-1] if throughs else None})


async def api_state(request):
    t = request.path_params["thread"]
    if not thread_ok(t):
        return Response(status_code=404)
    pr = await asyncio.to_thread(pr_info)
    return JSONResponse({"thread": t, "root": ROOT, "artifacts": artifact_list(t),
                         "through": pbcore.last_through(ROOT, t), "earlier": earlier_throughs(t), "pr": pr})


async def api_text(request):
    kind, t = request.path_params["kind"], request.path_params["thread"]
    if kind not in ("draft", "addendum") or not thread_ok(t):
        return Response(status_code=404)
    path = state_file(kind, t)
    if request.method == "PUT":
        body = await request.json()
        write_text(path, body.get("text", ""))
        if kind == "addendum":
            meta = pbcore.read_json(addendum_meta_path(t)) or {}
            meta["edited"] = True
            pbcore.write_json(addendum_meta_path(t), meta)
        return JSONResponse({"ok": True})
    result = {"text": read_text(path)}
    if kind == "addendum":
        meta = pbcore.read_json(addendum_meta_path(t)) or {}
        meta["stale"] = meta.get("basis") != publish_basis(t)
        meta["running"] = ("addendum", t) in JOBS and not JOBS[("addendum", t)].done()
        result["meta"] = meta
    return JSONResponse(result)


async def run_addendum(t):
    meta_path = addendum_meta_path(t)
    basis = publish_basis(t)
    HUB.send({"type": "addendum", "thread": t, "status": "running"})
    proc = await asyncio.create_subprocess_exec(
        sys.executable, os.path.join(PLUGIN_ROOT, "scripts", "addendum.py"), "--root", ROOT, "--thread", t,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode == 0:
        write_text(state_file("addendum", t), out.decode())
        pbcore.write_json(meta_path, {"basis": basis, "generated": pbcore.now_iso(), "edited": False})
        HUB.send({"type": "addendum", "thread": t, "status": "done"})
    else:
        meta = pbcore.read_json(meta_path) or {}
        meta["error"] = err.decode()[-2000:]
        pbcore.write_json(meta_path, meta)
        HUB.send({"type": "addendum", "thread": t, "status": "error", "error": meta["error"]})


async def api_addendum_generate(request):
    t = request.path_params["thread"]
    if not thread_ok(t):
        return Response(status_code=404)
    job = JOBS.get(("addendum", t))
    if job is None or job.done():
        JOBS[("addendum", t)] = asyncio.create_task(run_addendum(t))
    return JSONResponse({"ok": True})


def _render_local(text):
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough").render(text)


def render_markdown(text):
    pr = pr_info()
    args = ["gh", "api", "markdown", "-f", "mode=gfm", "-F", "text=@-"]
    if pr:
        args += ["-f", "context=" + pr["repo"]]
    try:
        cp = subprocess.run(args, input=text, capture_output=True, text=True, timeout=20, cwd=ROOT)
        if cp.returncode == 0:
            return cp.stdout, "github"
    except (OSError, subprocess.SubprocessError):
        pass
    return _render_local(text), "local"


async def api_render(request):
    body = await request.json()
    html, engine = await asyncio.to_thread(render_markdown, body.get("text", ""))
    # Local artifact paths resolve against the viewer's file route.
    html = html.replace('src="plots/', 'src="/files/plots/').replace('href="plots/', 'href="/files/plots/')
    return JSONResponse({"html": html, "engine": engine})


def publish_cmd(t, *extra):
    return [sys.executable, os.path.join(PLUGIN_ROOT, "scripts", "publish.py"), "--root", ROOT, "--thread", t, *extra]


def _publish_line(t, raw):
    line = raw.decode().rstrip("\n")
    try:
        rec = json.loads(line)
    except ValueError:
        rec = {"step": "output", "msg": line}
    HUB.send({"type": "publish", "thread": t, "log": rec})


async def run_publish(t, new_pr=None):
    HUB.send({"type": "publish", "thread": t, "log": {"step": "start", "msg": "publishing"}})
    cmd = publish_cmd(t)
    if new_pr is not None:
        spec = pbcore.state_dir(ROOT, "publish-%s-newpr.json" % t)
        pbcore.write_json(spec, new_pr)
        cmd += ["--create-pr", spec]
    # A plain detached process read by a daemon thread, not an asyncio subprocess: asyncio kills
    # its children when the viewer shuts down (e.g. at SessionEnd), and a publish must finish.
    loop = asyncio.get_running_loop()
    finished = loop.create_future()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)

    def pump():
        try:
            for raw in proc.stdout:
                loop.call_soon_threadsafe(_publish_line, t, raw)
            loop.call_soon_threadsafe(finished.set_result, proc.wait())
        except RuntimeError:  # the viewer's loop is gone; the publish carries on without it
            pass

    threading.Thread(target=pump, daemon=True).start()
    code = await finished
    _pr_cache["t"] = 0  # the PR may have just been created
    if code not in (0, 3):  # 3: no PR yet; the page asks for its title (the need_pr log line)
        HUB.send({"type": "publish", "thread": t, "log": {"error": "publish exited with status %d" % code}})


async def api_publish_run(request):
    t = request.path_params["thread"]
    if not thread_ok(t):
        return Response(status_code=404)
    job = JOBS.get(("publish", t))
    if job is not None and not job.done():
        return JSONResponse({"error": "a publish is already running for this thread"}, status_code=409)
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        return JSONResponse({"error": "bad request body"}, status_code=400)
    new_pr = body.get("create_pr")
    if new_pr is not None:
        if not (isinstance(new_pr, dict) and str(new_pr.get("title", "")).strip()):
            return JSONResponse({"error": "a PR title is required"}, status_code=400)
        new_pr = {"title": str(new_pr["title"]).strip(), "body": str(new_pr.get("body") or ""),
                  "draft": bool(new_pr.get("draft"))}
    JOBS[("publish", t)] = asyncio.create_task(run_publish(t, new_pr))
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- HTML / JS

PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__ · plotbook</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --accent:#0969da; --bg:#fff; }
* { box-sizing: border-box; }
/* The page is exactly one viewport tall: a flex column whose middle part absorbs the slack,
   so there is never a page-level scrollbar (100dvh where supported, 100vh otherwise). */
html, body { height:100%; }
body { display:flex; flex-direction:column; height:100vh; height:100dvh; overflow:hidden; }
body > header, #nav, body > .strip { flex:0 0 auto; }
body { margin:0; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; color:var(--fg); background:var(--bg); }
header { display:flex; gap:1em; align-items:center; padding:.5em 1em; border-bottom:1px solid var(--line); }
header h1 { font-size:1.1em; margin:0; } header .path { color:var(--muted); font-size:.85em; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
a { color:var(--accent); } button { font:inherit; padding:.3em .8em; border:1px solid var(--line); border-radius:6px; background:#f6f8fa; cursor:pointer; }
button.primary { background:#1f883d; color:#fff; border-color:#1a7f37; } button:disabled { opacity:.5; cursor:default; }
main.index { padding:1em 2em; flex:1 1 auto; overflow:auto; } .path { color:var(--muted); }
#stage { flex:1 1 auto; min-height:0; display:flex; align-items:center; justify-content:center; padding:.5em; position:relative; }
#stage img { width:100%; height:100%; object-fit:contain; background:#fff; }
#stage .card, .tray .card { border:1px solid var(--line); border-radius:8px; padding:1.5em; text-align:center; }
#nav { display:flex; gap:.5em; align-items:center; justify-content:center; padding:.25em; }
#nav .name { font-family: ui-monospace, monospace; min-width:24em; text-align:center; }
.strip { display:flex; gap:6px; overflow-x:auto; overflow-y:hidden; padding:6px 1em 10px; border-top:1px solid var(--line); align-items:stretch; overscroll-behavior-x:contain; }
.thumb { flex:0 0 auto; width:120px; border:2px solid transparent; border-radius:4px; cursor:pointer; display:flex; flex-direction:column; align-items:center; gap:4px; padding:2px 0 5px; background:#fff; }
.thumb img { width:116px; height:72px; object-fit:contain; }
.thumb .label { font: 10px/1.3 ui-monospace, monospace; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; width:116px; text-align:center; }
.thumb.current { border-color:var(--accent); }
.thumb .ext { width:116px; height:72px; display:flex; align-items:center; justify-content:center; font:600 13px ui-monospace, monospace; color:var(--muted); }
.divider { flex:0 0 auto; display:flex; align-items:center; border-left:3px dashed #cf222e; margin:0 4px; padding-left:2px; }
.divider.past { border-left-color:#afb8c1; margin:0 2px; padding-left:0; }
.divider span { writing-mode:vertical-rl; transform:rotate(180deg); font-size:10px; line-height:1; color:#cf222e; white-space:nowrap; }
.draft { flex:1 1 auto; min-height:0; display:grid; grid-template-columns: 1fr 1fr; grid-template-rows: minmax(0, 1fr) auto auto; gap:8px; padding:8px; }
.draft textarea { width:100%; height:100%; font: 13px/1.5 ui-monospace, monospace; padding:8px; border:1px solid var(--line); border-radius:6px; resize:none; }
.pane { display:flex; flex-direction:column; min-height:0; } .pane h2 { font-size:.85em; margin:0 0 4px; color:var(--muted); display:flex; gap:.5em; align-items:center; }
.preview { overflow:auto; border:1px solid var(--line); border-radius:6px; padding:16px; height:100%; }
.preview img { max-width:100%; }
.addendum { grid-column: 1 / 3; border:2px solid #8250df; border-radius:8px; padding:6px; background:#fbf8ff; }
.addendum textarea { height:150px; }
.tray { grid-column: 1 / 3; } .tray .strip { border:1px solid var(--line); border-radius:6px; }
.status { font-size:.85em; color:var(--muted); }
dialog { max-width:760px; width:90vw; border:1px solid var(--line); border-radius:8px; }
dialog pre { max-height:40vh; overflow:auto; background:#f6f8fa; padding:8px; font-size:12px; white-space:pre-wrap; }
#prdlg label { display:block; margin:.6em 0 .2em; font-weight:600; font-size:.9em; }
#prdlg input[type=text], #prdlg textarea { width:100%; box-sizing:border-box; font:inherit; padding:6px; border:1px solid var(--line); border-radius:6px; }
#prdlg textarea { height:8em; font: 13px/1.5 ui-monospace, monospace; resize:vertical; }
#prdlg label.check { font-weight:normal; display:flex; gap:.4em; align-items:center; }
#prdlg .buttons { display:flex; gap:.5em; justify-content:flex-end; margin-top:1em; }
</style>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown-light.css">
</head><body>
"""

THREAD_HTML = """
<header><h1>plotbook · __THREAD__</h1><span class="path" id="path"></span><a id="pr" target="_blank"></a>
<button onclick="location.href='/t/__THREAD__/draft'">Draft comment</button></header>
<div id="stage"></div>
<div id="nav"><button id="prev" title="previous (←)">◀</button><span class="name" id="name"></span><button id="next" title="next (→)">▶</button>
<button id="latest" title="jump to latest (End)">latest ⇥</button></div>
<div class="strip" id="strip"></div>
<script>const THREAD = "__THREAD__";</script>
"""

SHARED_JS = r"""
<script>
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fileUrl(a) { return "/files/plots/" + encodeURIComponent(a.name) + "?v=" + a.mtime; }
async function getState() { return (await fetch("/api/state/" + THREAD)).json(); }
function thumbHtml(a, cls) {
  const inner = a.image ? `<img loading="lazy" src="${fileUrl(a)}" alt="${esc(a.name)}">` : `<div class="ext">.${esc(a.ext)}</div>`;
  return `<div class="thumb ${cls}" data-name="${esc(a.name)}" title="${esc(a.name)}">${inner}<div class="label">${esc(a.name)}</div></div>`;
}
// Dividers: red dashed with a label after the latest comment's last artifact; grey dashed, unlabeled,
// after each earlier comment's. A boundary sits before the first artifact numbered above it.
function stripHtml(arts, through, earlier, currentName) {
  if (!arts.length) return "";
  if (through === 0) return `<div class="divider last"><span>nothing published</span></div>` +
    arts.map(a => thumbHtml(a, a.name === currentName ? "current" : "")).join("");
  const bounds = [...new Set([...(earlier || []), through])].sort((x, y) => x - y);
  const divider = b => b === through ? `<div class="divider last"><span>last comment</span></div>` : `<div class="divider past" title="earlier comment"></div>`;
  let html = "", i = 0;
  for (const a of arts) {
    while (i < bounds.length && a.n > bounds[i]) html += divider(bounds[i++]);
    html += thumbHtml(a, a.name === currentName ? "current" : "");
  }
  while (i < bounds.length) html += divider(bounds[i++]);
  return html;
}
// Thumbnail strips scroll horizontally. A vertical-only mouse wheel is turned into horizontal
// scrolling; input that is already mostly horizontal (trackpad swipes, tilt wheels, Shift+wheel,
// which browsers report as deltaX) is left to the browser's native scrolling.
function wheelToHorizontal(el) {
  el.addEventListener("wheel", e => {
    if (e.ctrlKey) return;                                   // pinch / Ctrl+wheel zoom
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;    // horizontal-dominant: native
    if (el.scrollWidth <= el.clientWidth) return;            // nothing to scroll
    const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? el.clientWidth : 1;  // lines / pages / pixels
    el.scrollLeft += (e.deltaY + e.deltaX) * unit;
    e.preventDefault();
  }, {passive: false});
}
document.querySelectorAll(".strip").forEach(wheelToHorizontal);
function onEvents(handler) {
  const es = new EventSource("/events");
  es.onmessage = ev => handler(JSON.parse(ev.data));
  return es;
}
function header(st) {
  const p = document.getElementById("path"); if (p) p.textContent = st.root;
  const pr = document.getElementById("pr");
  if (pr) { if (st.pr) { pr.href = st.pr.url; pr.textContent = "PR #" + st.pr.number; } else { pr.textContent = "(no PR for this branch)"; pr.removeAttribute("href"); } }
}
</script>
"""

THREAD_JS = r"""
<script>
let arts = [], idx = -1, through = 0, earlier = [];
function render() {
  const stage = document.getElementById("stage");
  const a = arts[idx];
  document.getElementById("name").textContent = a ? `${a.name}  (${idx + 1}/${arts.length})` : "no outputs yet";
  if (!a) { stage.innerHTML = '<div class="card">No outputs in plots/ for this thread yet.</div>'; }
  else if (a.image) { stage.innerHTML = `<img src="${fileUrl(a)}" alt="${esc(a.name)}">`; }
  else {
    const url = fileUrl(a);
    stage.innerHTML = `<div class="card"><div style="font:600 18px ui-monospace,monospace">${esc(a.name)}</div>
      <p><a href="${url}" download="${esc(a.name)}">download</a> · <a href="${url}" target="_blank">open</a></p>
      <p class="status">drag this card into another tool (e.g. geojson.io)</p></div>`;
    const card = stage.querySelector(".card"); card.draggable = true;
    card.addEventListener("dragstart", e => {
      const abs = location.origin + url;
      e.dataTransfer.setData("DownloadURL", `application/octet-stream:${a.name}:${abs}`);
      e.dataTransfer.setData("text/uri-list", abs);
    });
  }
  const strip = document.getElementById("strip");
  strip.innerHTML = stripHtml(arts, through, earlier, a && a.name);
  const cur = strip.querySelector(".current"); if (cur) cur.scrollIntoView({block: "nearest", inline: "nearest"});
}
function go(i) { if (i >= 0 && i < arts.length) { idx = i; render(); } }
async function refresh(initial) {
  const st = await getState(); header(st);
  const atLatest = idx === arts.length - 1;
  const curName = arts[idx] && arts[idx].name;
  arts = st.artifacts; through = st.through; earlier = st.earlier;
  if (initial || atLatest) idx = arts.length - 1;                // follow live output only if already at latest
  else { const j = arts.findIndex(a => a.name === curName); idx = j >= 0 ? j : Math.min(idx, arts.length - 1); }
  render();
}
document.getElementById("prev").onclick = () => go(idx - 1);
document.getElementById("next").onclick = () => go(idx + 1);
document.getElementById("latest").onclick = () => go(arts.length - 1);
document.getElementById("strip").onclick = e => { const t = e.target.closest(".thumb"); if (t) go(arts.findIndex(a => a.name === t.dataset.name)); };
document.addEventListener("keydown", e => {
  if (e.key === "ArrowLeft") go(idx - 1); else if (e.key === "ArrowRight") go(idx + 1);
  else if (e.key === "End") go(arts.length - 1); else if (e.key === "Home") go(0);
});
onEvents(ev => { if ((ev.type === "plots" && ev.threads.includes(THREAD)) || ev.type === "published") refresh(false); });
refresh(true);
</script></body></html>
"""

DRAFT_HTML = """
<header><h1>plotbook · __THREAD__ · draft comment</h1><span class="path" id="path"></span><a id="pr" target="_blank"></a>
<a href="/t/__THREAD__">back to thread</a><button class="primary" id="publish">Publish</button></header>
<div class="draft">
  <div class="pane"><h2>Your comment (markdown) <span class="status" id="saved"></span></h2>
    <textarea id="draft" placeholder="Write the narrative here. Click a thumbnail below to insert that plot at the cursor."></textarea></div>
  <div class="pane"><h2>Preview <span class="status" id="engine"></span></h2><div class="preview markdown-body" id="preview"></div></div>
  <div class="addendum pane"><h2>🤖 Extra details 🤖 — published verbatim inside a collapsed &lt;details&gt; block (empty = omitted)
    <span class="status" id="addstatus"></span><button id="regen">Regenerate</button></h2>
    <textarea id="addendum" placeholder="(generating…)"></textarea></div>
  <div class="tray"><div class="strip" id="tray"></div></div>
</div>
<dialog id="logdlg"><h3 id="logtitle">Publishing…</h3><pre id="log"></pre><p id="result"></p><p><button id="closelog" disabled>OK</button></p></dialog>
<dialog id="prdlg"><form id="prform" method="dialog"><h3>Open a pull request</h3><p id="prwhy"></p>
  <label for="prtitle">Title</label><input type="text" id="prtitle" required>
  <label for="prbody">Description (optional, markdown)</label><textarea id="prbody"></textarea>
  <label class="check"><input type="checkbox" id="prdraft"> Draft pull request</label>
  <div class="buttons"><button type="button" id="prcancel">Cancel</button><button class="primary" id="prok">Create PR and publish</button></div>
</form></dialog>
<script>const THREAD = "__THREAD__";</script>
<script>window.MathJax = { tex: { inlineMath: [['$','$']], displayMath: [['$$','$$']] }, startup: { typeset: false } };</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
"""

DRAFT_JS = r"""
<script>
const draft = document.getElementById("draft"), addendum = document.getElementById("addendum");
let arts = [], through = 0, earlier = [], timers = {};
function debounce(key, ms, fn) { clearTimeout(timers[key]); timers[key] = setTimeout(fn, ms); }
async function save(kind, text) {
  await fetch(`/api/${kind}/${THREAD}`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text})});
  if (kind === "draft") document.getElementById("saved").textContent = "saved";
}
async function renderPreview() {
  const r = await (await fetch("/api/render", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: draft.value})})).json();
  const pv = document.getElementById("preview");
  pv.innerHTML = r.html;
  document.getElementById("engine").textContent = r.engine === "github" ? "(GitHub renderer)" : "(offline renderer — approximate)";
  pv.querySelectorAll('section[data-type="mermaid"], .highlight-source-mermaid, pre[lang="mermaid"], code.language-mermaid').forEach(el => {
    if (!el.isConnected) return;  // already replaced via an enclosing container
    const src = el.matches("section") ? (el.querySelector("pre") || el).textContent : el.textContent;
    const d = document.createElement("div"); d.className = "mermaid"; d.textContent = src;
    (el.matches("code") ? el.parentElement : el).replaceWith(d);
  });
  try { if (window.mermaid) await mermaid.run({nodes: pv.querySelectorAll(".mermaid")}); } catch (e) { console.warn(e); }
  if (window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([pv]).catch(console.warn);
}
draft.addEventListener("input", () => { document.getElementById("saved").textContent = "…";
  debounce("save", 400, () => save("draft", draft.value)); debounce("render", 700, renderPreview); });
addendum.addEventListener("input", () => debounce("asave", 400, () => save("addendum", addendum.value)));
function insertAtCursor(text) {
  const s = draft.selectionStart, e = draft.selectionEnd, v = draft.value;
  const before = v.slice(0, s), pre = before && !before.endsWith("\n\n") ? (before.endsWith("\n") ? "\n" : "\n\n") : "";
  draft.value = before + pre + text + "\n\n" + v.slice(e);
  const pos = (before + pre + text + "\n\n").length; draft.focus(); draft.setSelectionRange(pos, pos);
  draft.dispatchEvent(new Event("input"));
}
document.getElementById("tray").onclick = e => {
  const t = e.target.closest(".thumb"); if (!t) return;
  const a = arts.find(x => x.name === t.dataset.name);
  insertAtCursor(a.image ? `<img src="plots/${a.name}" alt="${a.name}" width="600">` : `[${a.name}](plots/${a.name})`);
};
async function refreshTray() {
  const st = await getState(); header(st); arts = st.artifacts; through = st.through; earlier = st.earlier;
  const tray = document.getElementById("tray"); tray.innerHTML = stripHtml(arts, through, earlier, null);
  const firstNew = tray.querySelector(".divider.last"); if (firstNew) firstNew.scrollIntoView({inline: "center", block: "nearest"});
}
function addStatus(msg) { document.getElementById("addstatus").textContent = msg; }
async function loadAddendum(autostart) {
  const r = await (await fetch(`/api/addendum/${THREAD}`)).json();
  addendum.value = r.text;
  if (r.meta.running) addStatus("⏳ generating… (keep writing; it fills in when ready)");
  else if (r.meta.error) addStatus("⚠ generation failed: " + r.meta.error.slice(-200));
  else addStatus(r.meta.generated ? "generated " + r.meta.generated + (r.meta.edited ? " · edited" : "") : "");
  if (autostart && r.meta.stale && !r.meta.running) generate();
}
async function generate() {
  addStatus("⏳ generating… (keep writing; it fills in when ready)");
  await fetch(`/api/addendum/${THREAD}/generate`, {method: "POST"});
}
document.getElementById("regen").onclick = async () => {
  const r = await (await fetch(`/api/addendum/${THREAD}`)).json();
  if (r.text.trim() && r.meta.edited && !confirm("Replace your edited Extra-details text with a fresh generation?")) return;
  generate();
};
// Publish runs immediately (no confirmation). The progress dialog's OK returns to the thread
// view after a successful publish, or back to the draft after a failure so it can be fixed.
const logDlg = document.getElementById("logdlg"), publishBtn = document.getElementById("publish"), okBtn = document.getElementById("closelog");
let publishState = "idle";  // idle | running | done | failed
function finishPublish(state, title, resultHtml) {
  publishState = state;
  document.getElementById("logtitle").textContent = title;
  document.getElementById("result").innerHTML = resultHtml;
  okBtn.disabled = false; okBtn.focus();
}
async function startPublish(createPr) {
  if (publishState === "running") return;
  publishState = "running"; publishBtn.disabled = true; okBtn.disabled = true;
  document.getElementById("logtitle").textContent = "Publishing…";
  document.getElementById("log").textContent = ""; document.getElementById("result").textContent = "";
  logDlg.showModal();
  await save("draft", draft.value); await save("addendum", addendum.value);
  const r = await (await fetch(`/api/publish/${THREAD}/run`, {method: "POST", headers: {"Content-Type": "application/json"},
                                                              body: JSON.stringify(createPr ? {create_pr: createPr} : {})})).json();
  if (r.error) finishPublish("failed", "Publish failed", `<b style="color:#cf222e">${esc(r.error)}</b>`);
}
publishBtn.onclick = () => startPublish(null);
// No PR for this branch yet: publish.py stops before changing anything and reports what creating one
// involves; ask for a title (and optional description), then publish again with it.
const prDlg = document.getElementById("prdlg"), prTitle = document.getElementById("prtitle");
function askForPr(setup) {
  publishState = "idle"; logDlg.close();
  const b = `<code>${esc(setup.branch)}</code>`, r = `<code>${esc(setup.remote)}</code>`;
  document.getElementById("prwhy").innerHTML = (setup.on_remote
    ? `Branch ${b} is on ${r} but has no pull request yet. Publishing opens one`
    : `Branch ${b} isn't on ${r} yet. Publishing pushes it, opens a pull request`) +
    ` into <code>${esc(setup.base)}</code> of <code>${esc(setup.repo)}</code>, then posts this comment on it.`;
  if (!prTitle.value) prTitle.value = setup.title || "";
  prDlg.showModal(); prTitle.focus(); prTitle.select();
}
document.getElementById("prcancel").onclick = () => prDlg.close();
document.getElementById("prform").addEventListener("submit", e => {
  e.preventDefault();
  if (!prTitle.value.trim()) { prTitle.focus(); return; }
  prDlg.close();
  startPublish({title: prTitle.value.trim(), body: document.getElementById("prbody").value,
                draft: document.getElementById("prdraft").checked});
});
logDlg.addEventListener("cancel", e => { if (publishState === "running") e.preventDefault(); });  // Esc mid-publish
logDlg.addEventListener("close", () => {
  if (publishState === "done") { location.href = `/t/${THREAD}`; return; }
  publishState = "idle"; publishBtn.disabled = false;
});
okBtn.onclick = () => logDlg.close();
onEvents(async ev => {
  if (ev.type === "plots" && ev.threads.includes(THREAD)) refreshTray();
  if (ev.type === "published") refreshTray();
  if (ev.type === "addendum" && ev.thread === THREAD) {
    if (ev.status === "done") loadAddendum(false);
    else if (ev.status === "error") addStatus("⚠ generation failed: " + (ev.error || "").slice(-200));
  }
  if (ev.type === "publish" && ev.thread === THREAD) {
    const l = ev.log, log = document.getElementById("log");
    if (l.need_pr) { askForPr(l.need_pr); return; }
    if (l.msg) log.textContent += (l.step ? `[${l.step}] ` : "") + l.msg + "\n";
    if (l.error) {
      log.textContent += "ERROR: " + l.error + "\n";
      finishPublish("failed", "Publish failed", '<b style="color:#cf222e">Publish failed.</b> Nothing is lost; fix the problem and press Publish again.');
    }
    if (l.done) finishPublish("done", "Published", `✅ <a href="${esc(l.url)}" target="_blank">${esc(l.url)}</a>`);
  }
});
(async () => {
  if (window.mermaid) mermaid.initialize({startOnLoad: false});
  draft.value = (await (await fetch(`/api/draft/${THREAD}`)).json()).text;
  renderPreview(); refreshTray(); loadAddendum(true);
})();
</script></body></html>
"""

routes = [
    Route("/", index),
    Route("/health", health),
    Route("/events", events),
    Route("/files/{path:path}", files),
    Route("/t/{thread}", thread_page),
    Route("/t/{thread}/draft", draft_page),
    Route("/api/state/{thread}", api_state),
    Route("/api/render", api_render, methods=["POST"]),
    Route("/api/addendum/{thread}/generate", api_addendum_generate, methods=["POST"]),
    Route("/api/publish/{thread}/run", api_publish_run, methods=["POST"]),
    Route("/api/{kind}/{thread}", api_text, methods=["GET", "PUT"]),
]


@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(watch_files())
    yield
    task.cancel()


app = Starlette(routes=routes, lifespan=lifespan)


def main():
    global ROOT
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd(), help="analysis directory (contains .plotbook/)")
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    args = ap.parse_args()
    root = pbcore.find_root(args.root)
    if root is None:
        sys.exit("viewer: %s is not inside a plotbook analysis" % args.root)
    ROOT = os.path.realpath(root)
    import socket

    import uvicorn

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]
    pbcore.write_json(pbcore.state_dir(ROOT, "viewer.json"), {"pid": os.getpid(), "port": port, "started": pbcore.now_iso()})
    print("plotbook viewer for %s at http://127.0.0.1:%d" % (ROOT, port), flush=True)
    config = uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1)
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
