#!/bin/sh
# Codex Desktop launches hooks with a GUI PATH that may not include uv's bin
# directory. The installer writes the resolved, non-secret tool-bin location.
set -u

event=${1:-}
case "$event" in
  session-start|prompt|stop) ;;
  *)
    printf '%s\n' '{"continue":true}'
    exit 0
    ;;
esac

pointer="$HOME/.config/dduo-solo-founder/hook-runtime-bin"
runtime_bin=""
if [ -r "$pointer" ]; then
  IFS= read -r runtime_bin < "$pointer" || true
fi

# This preserves compatibility with a previous successful uv tool install if
# the pointer was not yet written, while still avoiding Codex's GUI PATH.
if [ -z "$runtime_bin" ]; then
  runtime_bin="$HOME/.local/bin"
fi

dispatcher="$runtime_bin/dduo-solo-founder-hook-dispatch"
if [ -x "$dispatcher" ]; then
  exec "$dispatcher" "$event"
fi

# Hooks must always return syntactically valid JSON. A missing local runtime
# or dispatcher must never surface a shell error or let an interrupted legacy
# runtime emit an envelope the current client rejects.
case "$event" in
  session-start)
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"dDuo local memory is temporarily unavailable. Continue normally; do not claim this turn was recorded."}}'
    ;;
  prompt)
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"dDuo local memory is temporarily unavailable. Continue normally; do not claim this turn was recorded."}}'
    ;;
  stop)
    printf '%s\n' '{"continue":true}'
    ;;
esac
