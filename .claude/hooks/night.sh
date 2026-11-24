#!/bin/sh
# 21:30-4:30 on the Mac's own clock: remind Claude that it is Tatsuki's bedtime (.claude/rules/night.md).
# Only on the Mac: a cloud container's clock is not where Tatsuki is.
[ "$(uname)" = "Darwin" ] || exit 0
h=$(date +%H); m=$(date +%M)
min=$(( ${h#0} * 60 + ${m#0} ))
if [ "$min" -ge 1290 ] || [ "$min" -lt 270 ]; then
  echo "It is $(date +%H:%M) where Tatsuki is: bedtime (21:30-4:30). Follow .claude/rules/night.md: first line tells him you will carry on and he should sleep."
fi
exit 0
