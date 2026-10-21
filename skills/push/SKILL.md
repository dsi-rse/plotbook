---
description: Open the plotbook draft-comment view for this session's thread.
disable-model-invocation: true
allowed-tools: Bash(plotbook-viewer *)
---
The user wants to write a PR comment for this thread. Find the thread name in the latest
`[plotbook] thread=...` context line, then run exactly:

    plotbook-viewer <thread> --draft --open

Reply with only the URL it printed, and say that publishing happens from that page: the user
writes the comment there, edits the "🤖 Extra details" pane, and presses Publish. Do not compose
the comment yourself, and do not run git or gh. Publishing is the user's action alone.
