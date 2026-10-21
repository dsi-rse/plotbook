# plotbook root delta: run-from-root guard, matplotlib bootstrap, save() helper.
# Never edit this file; put changes in a new delta (shared_NNNN.py) that execs its parent.
import os as _os
import sys as _sys

if not (_os.path.isdir("plots") and _os.path.isdir("scripts")):
    _sys.exit(
        "plotbook: run from the analysis root, e.g.  python scripts/<name>.py\n"
        "(current directory %s has no plots/ and scripts/)" % _os.getcwd()
    )

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Keep SVG text as <text> elements (editable in Inkscape) in a plain font: DejaVu Sans (bundled
# with matplotlib), written as `font-family: 'DejaVu Sans', sans-serif` so viewers without it fall
# back to their own plain sans-serif. Make output deterministic so reproductions can be diffed.
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["svg.hashsalt"] = "plotbook"


def _plotbook_script():
    main = _sys.modules.get("__main__")
    path = getattr(main, "__file__", None) or (_sys.argv[0] if _sys.argv and _sys.argv[0] else "")
    return _os.path.relpath(_os.path.abspath(path)) if path else "unknown"


def _plotbook_provenance(script):
    import datetime
    import json
    import re
    import subprocess

    info = {"script": script, "leaf": None, "commit": "unknown", "dirty": None, "session": None,
            "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    canon = re.compile(r'^exec\(open\("scripts/(shared_\d{4}\.py)"\)\.read\(\)\)\s*$')
    try:
        with open(script) as f:
            for line in f:
                m = canon.match(line)
                if m:
                    info["leaf"] = "scripts/" + m.group(1)
                    break
    except OSError:
        pass
    try:
        run = lambda *a: subprocess.run(["git"] + list(a), capture_output=True, text=True, timeout=10)
        sha = run("rev-parse", "HEAD")
        if sha.returncode == 0:
            info["commit"] = sha.stdout.strip()
            st = run("status", "--porcelain", "--", script)
            info["dirty"] = bool(st.stdout.strip()) if st.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    m = re.match(r"^([A-Za-z](?:[A-Za-z_-]*[A-Za-z])?)\d{4}-", _os.path.basename(script))
    if m:
        try:
            with open(_os.path.join(".plotbook", "state", "claims", m.group(1) + ".json")) as f:
                info["session"] = json.load(f).get("session_id")
        except (OSError, ValueError):
            pass
    return json.dumps(info, sort_keys=True)


def output_path(ext="svg", suffix=None):
    """plots/<script-stem>[-suffix].<ext> -- use for non-matplotlib outputs (e.g. geojson)."""
    stem = _os.path.splitext(_os.path.basename(_plotbook_script()))[0]
    if suffix:
        stem = "%s-%s" % (stem, suffix)
    return _os.path.join("plots", "%s.%s" % (stem, ext))


def save(fig=None, suffix=None):
    """Save `fig` (default: current figure) as plots/<script-stem>[-suffix].svg with provenance."""
    fig = fig if fig is not None else plt.gcf()
    path = output_path("svg", suffix)
    fig.savefig(path, format="svg", bbox_inches="tight",
                metadata={"Description": _plotbook_provenance(_plotbook_script()), "Date": None})
    print("saved", path)
    return path
