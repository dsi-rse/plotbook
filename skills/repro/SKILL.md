---
description: Reproduce an old plotbook artifact from its commit in a temporary git worktree.
argument-hint: <artifact, e.g. primary0003 or primary0003-foo.svg>
disable-model-invocation: true
allowed-tools: Bash(plotbook-repro *)
---
Run exactly:

    plotbook-repro $ARGUMENTS

It finds the commit that produced the artifact and re-runs its script in a temporary
`git worktree`, from that worktree's analysis root. The working tree and its uncommitted
files are not touched. Report what it prints: the commit, the reproduced output path, and
whether the result matches the committed artifact. If it differs or fails, explain the
likely cause in a sentence or two (e.g. data files changed outside git). Do not run git yourself.
