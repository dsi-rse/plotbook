import json
import multiprocessing
import os
import re
import subprocess
import sys

import pytest
from conftest import PLUGIN

import alloc_delta
import claim_thread
import pbcore


@pytest.fixture
def analysis(repo):
    root = str(repo / "analysis")
    claim_thread.scaffold(root)
    return root


def write(root, rel, text):
    with open(os.path.join(root, rel), "w") as f:
        f.write(text)


def run(root, rel, cwd=None):
    return subprocess.run([sys.executable, rel], cwd=cwd or root, capture_output=True, text=True)


def test_late_binding_and_branch_and_return(analysis):
    # root-level helpers: describe() calls scale() -- an override of scale() must be seen by describe().
    with open(os.path.join(analysis, "scripts", "shared_0000.py"), "a") as f:
        f.write("\ndef scale():\n    return 1\n\ndef describe():\n    return 'scale=%d' % scale()\n")
    m1, _ = alloc_delta.allocate(analysis, "primary", 0)
    with open(os.path.join(analysis, "scripts", "shared_%04d.py" % m1), "a") as f:
        f.write("def scale():\n    return 2\n")  # the tangent
    m2, _ = alloc_delta.allocate(analysis, "primary", 0)  # back to pre-tangent assumptions
    with open(os.path.join(analysis, "scripts", "shared_%04d.py" % m2), "a") as f:
        f.write("def scale():\n    return 3\n")
    m3, _ = alloc_delta.allocate(analysis, "primary", m1)  # continue the tangent
    with open(os.path.join(analysis, "scripts", "shared_%04d.py" % m3), "a") as f:
        f.write("def extra():\n    return describe() + '!'\n")

    for leaf, expected in [(0, "scale=1"), (m1, "scale=2"), (m2, "scale=3"), (m3, "scale=2!")]:
        rel = "scripts/primary%04d-check.py" % (leaf + 1)
        call = "extra()" if leaf == m3 else "describe()"
        write(analysis, rel, "import sys\n%s\nprint(%s)\n" % (pbcore.canonical_exec_line(leaf), call))
        r = run(analysis, rel)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == expected
        assert pbcore.script_leaf(os.path.join(analysis, rel)) == leaf

    # The tree is recoverable from first lines alone.
    assert pbcore.delta_tree(analysis) == {0: None, m1: 0, m2: 0, m3: m1}
    assert pbcore.delta_chain(pbcore.delta_tree(analysis), m3) == [0, m1, m3]


def test_plain_import_of_delta_merges_into_module(analysis):
    m1, _ = alloc_delta.allocate(analysis, "primary", 0)
    with open(os.path.join(analysis, "scripts", "shared_%04d.py" % m1), "a") as f:
        f.write("ANSWER = 42\n")
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'scripts'); "
                        "import shared_%04d as s; print(s.ANSWER, callable(s.save))" % m1],
                       cwd=analysis, capture_output=True, text=True)
    assert r.stdout.split() == ["42", "True"], r.stderr


def test_missing_parent_and_malformed_line_are_loud(analysis):
    write(analysis, "scripts/shared_0005.py", pbcore.canonical_exec_line(4) + "\n")
    with pytest.raises(pbcore.PlotbookError, match="does not exist"):
        pbcore.delta_tree(analysis)
    write(analysis, "scripts/shared_0005.py", "exec(open('scripts/shared_0000.py').read())\n")
    with pytest.raises(pbcore.PlotbookError, match="first line must be exactly"):
        pbcore.delta_tree(analysis)
    with pytest.raises(pbcore.PlotbookError, match="exec line must be exactly"):
        pbcore.script_leaf_from_text("exec(open('scripts/shared_0000.py').read())\n")
    # In a compile-style analysis, the legacy form is readable but not accepted for new code.
    legacy = pbcore.canonical_exec_line(0, "legacy") + "\n"
    assert pbcore.script_leaf_from_text(legacy) == 0
    with pytest.raises(pbcore.PlotbookError, match=r'exactly exec\(compile\(open\(_f := "scripts/shared_NNNN.py"'):
        pbcore.script_leaf_from_text(legacy, style=pbcore.exec_style(analysis))


def test_traceback_names_the_delta_file(analysis):
    m1, rel1 = alloc_delta.allocate(analysis, "primary", 0)
    with open(os.path.join(analysis, rel1), "a") as f:
        f.write("def g():\n    return 1 / 0\n")
    m2, rel2 = alloc_delta.allocate(analysis, "primary", m1)
    write(analysis, "scripts/primary0001-x.py", pbcore.canonical_exec_line(m2) + "\ng()\n")
    r = run(analysis, "scripts/primary0001-x.py")
    assert 'File "%s", line 3, in g' % rel1 in r.stderr and "return 1 / 0" in r.stderr
    assert "<string>" not in r.stderr
    # An error while loading the chain names every level of it.
    with open(os.path.join(analysis, rel1), "a") as f:
        f.write("undefined_name\n")
    r = run(analysis, "scripts/primary0001-x.py")
    assert 'File "%s", line 1, in <module>' % rel2 in r.stderr
    assert 'File "%s", line 4, in <module>' % rel1 in r.stderr and "<string>" not in r.stderr


def test_legacy_analysis_keeps_legacy_exec_line(analysis):
    # An analysis scaffolded before the compile() form: its immutable root delta knows only the old line.
    root0 = os.path.join(analysis, "scripts", "shared_0000.py")
    text = open(root0).read()
    text = text.replace(text[text.index("# Never edit"):text.index("import os")], "# Never edit this file.\n")
    write(analysis, "scripts/shared_0000.py", text.replace("(?:compile\\(open\\(_f := |open\\()", "open\\("))
    assert pbcore.exec_style(analysis) == "legacy"
    m, rel = alloc_delta.allocate(analysis, "primary", 0)
    assert open(os.path.join(analysis, rel)).read() == 'exec(open("scripts/shared_0000.py").read())\n'
    assert pbcore.delta_tree(analysis) == {0: None, m: 0}
    with pytest.raises(pbcore.PlotbookError, match=r'exactly exec\(open\("scripts/shared_NNNN.py"\)'):
        pbcore.script_leaf_from_text(pbcore.canonical_exec_line(m) + "\n", style="legacy")


def test_run_from_root_guard_and_svg_output(analysis):
    write(analysis, "scripts/primary0001-demo.py",
          "import os, sys\n"
          "if not os.path.isdir('plots'): sys.exit('plotbook: run from the analysis root: python scripts/' + os.path.basename(__file__))\n"
          + pbcore.canonical_exec_line(0) + "\n"
          "fig, ax = plt.subplots()\nax.plot([1, 2, 3])\nax.set_title('Penguins')\nsave(fig)\n")
    r = run(analysis, "primary0001-demo.py", cwd=os.path.join(analysis, "scripts"))
    assert r.returncode != 0 and "run from the analysis root" in r.stderr
    r = run(analysis, "scripts/primary0001-demo.py")
    assert r.returncode == 0, r.stderr
    svg = open(os.path.join(analysis, "plots", "primary0001-demo.svg")).read()
    assert re.search(r"<text[^>]*>Penguins</text>", svg)  # editable text, not paths
    assert "DejaVu Sans" in svg
    desc = re.search(r"<dc:description>(.*?)</dc:description>", svg, re.S).group(1)
    prov = json.loads(desc.replace("&quot;", '"'))
    assert prov["script"] == "scripts/primary0001-demo.py"
    assert prov["leaf"] == "scripts/shared_0000.py"
    assert prov["dirty"] is True and len(prov["commit"]) == 40


def _alloc(args):
    root, i = args
    return alloc_delta.allocate(root, "t%s" % "abcdefgh"[i], 0)[0]


def test_concurrent_allocation_is_distinct(analysis):
    with multiprocessing.get_context("fork").Pool(8) as pool:
        nums = pool.map(_alloc, [(analysis, i) for i in range(8)] * 3)
    assert sorted(nums) == list(range(1, 25))
    tree = pbcore.delta_tree(analysis)
    assert all(tree[m] == 0 for m in nums)
    owners = pbcore.delta_owners(analysis)
    assert len(owners) == 24


def test_cli(analysis):
    r = subprocess.run([os.path.join(PLUGIN, "bin", "plotbook-delta"), "--thread", "primary", "--parent", "0000"],
                       cwd=analysis, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "scripts/shared_0001.py"
    assert open(os.path.join(analysis, "scripts/shared_0001.py")).read() == pbcore.canonical_exec_line(0) + "\n"
