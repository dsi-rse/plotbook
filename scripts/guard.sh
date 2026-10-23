#!/bin/sh
# plotbook scope guard: every hook goes through here. Exits 0 immediately (no Python
# startup) unless the session's directory is inside an analysis (has .plotbook/ above it) or
# inside a git repo where a session has started one elsewhere (<git-common-dir>/plotbook/sessions,
# e.g. Claude Code at the repo root with `/plotbook:start T <analysis-dir>`).
# The one exception: a `/plotbook:start` prompt, which is what creates .plotbook/.
# usage: guard.sh <script.py>   (hook JSON on stdin is passed through)
[ -n "$PLOTBOOK_HEADLESS" ] && exit 0
script=$1
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
d=$PWD
while :; do
    if [ -d "$d/.plotbook" ]; then
        exec python3 "$here/$script"
    fi
    [ -e "$d/.git" ] && break  # analyses live inside their repo
    [ "$d" = / ] && break
    d=$(dirname -- "$d")
done
gd=$(git rev-parse --git-common-dir 2>/dev/null) && [ -d "$gd/plotbook/sessions" ] && exec python3 "$here/$script"
if [ "$script" = turn_start.py ]; then
    input=$(cat)
    case $input in
        # Not `| exec`: the pipeline runs in a subshell, and its status (2 = block the prompt) must be ours.
        *'/plotbook:start'*) printf '%s' "$input" | python3 "$here/$script"; exit $? ;;
    esac
fi
exit 0
