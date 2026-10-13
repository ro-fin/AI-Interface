#!/bin/sh
# Installs Cameo on Ubuntu 22.04 / 24.04 (other Debian-based X11 desktops work the same way):
# system packages, a Python environment (.venv), the packages, the voice model, and a Cameo launcher
# in the applications menu and on the desktop.
set -e
ROOT="$(dirname "$(readlink -f "$0")")"
cd "$ROOT"

echo "Installing system packages (asks for your password)..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-tk libportaudio2 libgl1 libglib2.0-0 zenity

PY=python3
for v in 3.12 3.11 3.10; do
    if command -v "python$v" >/dev/null 2>&1; then PY="python$v"; break; fi
done
"$PY" -c 'import sys; assert (3, 10) <= sys.version_info[:2] <= (3, 12), sys.version' \
    || { echo "Python 3.10-3.12 is required (MediaPipe has no wheels for newer versions)." >&2; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/setup_models.py
chmod +x start-cameo.sh

# The camera needs the "video" group.
if ! id -nG | grep -qw video; then
    sudo usermod -aG video "$USER"
    echo "Added you to the 'video' group: log out and back in once before starting Cameo."
fi

# Menu entry and desktop icon (Exec is quoted: the folder name may contain spaces or '&').
ENTRY="$HOME/.local/share/applications/cameo.desktop"
mkdir -p "$(dirname "$ENTRY")"
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=Cameo
Comment=Control the desktop with hand signs and voice
Exec="$ROOT/start-cameo.sh"
Path=$ROOT
Icon=input-gaming
Terminal=false
Categories=Utility;Accessibility;
EOF
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP" ]; then
    cp "$ENTRY" "$DESKTOP/cameo.desktop"
    chmod +x "$DESKTOP/cameo.desktop"
    gio set "$DESKTOP/cameo.desktop" metadata::trusted true 2>/dev/null || true  # GNOME: "Allow Launching"
fi

.venv/bin/python -m cameo doctor || true
if [ "$XDG_SESSION_TYPE" = "wayland" ]; then
    echo
    echo "You are in a Wayland session. Cameo needs X11: log out, click the gear icon on the login"
    echo "screen, choose \"Ubuntu on Xorg\", and log in again."
fi
echo "Cameo is installed. Start it from the applications menu or the desktop icon, or run ./start-cameo.sh"
