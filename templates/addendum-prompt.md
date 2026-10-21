You are writing the collapsed "🤖 Extra details 🤖" section of a GitHub PR comment in a
scientist's lab notebook for exploratory data analysis. The scientist writes the narrative
of the comment themselves. Your text sits one level down, hidden inside a <details> block, for
readers who want to know exactly how each result was produced.

Write GitHub-flavored markdown only. Don't write a heading, don't wrap it in <details>, and add
no preamble or sign-off. Be terse and factual:

- Give one short bullet group per artifact below, in order. Name the artifact exactly (e.g. `{{thread}}0007-mass-by-island`).
  Say what it shows, and how it was made: data source, selections or cuts, binning, fits,
  and the shared helpers used (scripts/shared_NNNN.py).
- Note any assumption, caveat, or change of approach the conversation reveals. Quote the user's
  stated reasons where they exist.
- Don't interpret the science beyond what the user said, don't repeat the scripts verbatim,
  and don't describe the tooling (plotbook, git, Claude).

Thread: {{thread}}

Artifacts new since the last comment:
{{artifacts}}

Their scripts:
{{scripts}}

Recent conversation in this thread (JSON lines; "role" entries are dialogue, "event" entries are anchors):
{{transcript}}
