#!/bin/sh
# Starts Cameo on Linux (X11 session). Made by install.sh; also used by the Cameo menu entry.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Cameo is not installed yet: run ./install.sh" >&2
    exit 1
fi
exec .venv/bin/python -m cameo run "$@"
