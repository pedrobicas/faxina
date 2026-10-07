#!/usr/bin/env sh
# Mac e Linux: ./iniciar.sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 faxina.py "$@"
fi
exec python faxina.py "$@"
