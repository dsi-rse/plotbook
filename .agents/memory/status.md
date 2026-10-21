# Status (2026-10-21)

Done: everything in the spec's layout, plus bin/ shims, marketplace.json, and tests/ (pytest, 23 tests, all passing).
Verified end to end with headless `claude -p --plugin-dir` sessions under `deny Bash(git *)/(gh *)`:
- start, first plot, and a scoped commit;
- concurrent primary/tangent sessions, each allocating a delta;
- viewer live update (~10 ms), the no-yank rule, the draft view with GitHub preview + MathJax, and addendum prefill.

Pending:
- live GitHub run on jpivarski/penguin-science (private), acceptance checks 5–7 and 9. Confirm with the user before
  the first push/comment there; the user does the squash-merge and branch deletion for check 7;
- mermaid preview fix (highlight-source-mermaid), not yet re-checked in the browser;
- the external entry snippet for the dsi-rse/skills marketplace (the user applies it);
- UI polish later (the spec expects iteration).
