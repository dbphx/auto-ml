#!/bin/sh
set -eu

CODEX_HOME=${CODEX_HOME:-/home/appuser/.codex}
AUTH_SOURCE=${CODEX_HOST_AUTH_FILE:-/run/codex-host-auth.json}
AUTH_TARGET="$CODEX_HOME/auth.json"

if [ "$(id -u)" -eq 0 ]; then
    install -d -o appuser -g appuser "$CODEX_HOME"
    if [ -s "$AUTH_SOURCE" ] && { [ ! -s "$AUTH_TARGET" ] || [ "$AUTH_SOURCE" -nt "$AUTH_TARGET" ]; }; then
        install -m 600 -o appuser -g appuser "$AUTH_SOURCE" "$AUTH_TARGET"
    fi
    chown -R appuser:appuser "$CODEX_HOME"
    exec runuser -u appuser -- "$@"
fi
mkdir -p "$CODEX_HOME"
exec "$@"
