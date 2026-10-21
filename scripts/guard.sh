#!/bin/sh
# plotbook scope guard: every hook goes through here. Exits 0 immediately (no Python
# startup) unless the session's directory is inside an analysis (has .plotbook/ above it).
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
    [ "$d" = / ] && break
    d=$(dirname -- "$d")
done
if [ "$script" = turn_start.py ]; then
    input=$(cat)
    case $input in
        *'/plotbook:start'*) printf '%s' "$input" | exec python3 "$here/$script" ;;
    esac
fi
exit 0
