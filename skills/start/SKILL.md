---
description: Claim a plotbook thread for this session, scaffold the analysis directory, and start the viewer.
argument-hint: <thread> [--steal]
disable-model-invocation: true
---
The user ran `/plotbook:start $ARGUMENTS`. plotbook's UserPromptSubmit hook has already claimed
the thread, scaffolded the analysis directory, and started the viewer, deterministically, before
this message. Its `[plotbook]` lines are in your context above.

Reply in two or three short lines: the thread name, the viewer URL exactly as the hook printed it
(or the hook's note if the viewer couldn't start), and the next artifact name. Do not run any
commands, and don't summarize the conventions back to the user.

From now on, follow the `plotbook:conventions` skill for every request in this session. Read it now.
