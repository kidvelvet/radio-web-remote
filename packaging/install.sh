#!/usr/bin/env sh
# Install or upgrade radio-web-remote for the current user (Linux, macOS).
# No admin rights needed, except for the Linux packages and groups noted below.
#
#   curl -fsSL https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.sh | sh
#   sh install.sh --service      # also start it at login and keep it running
#   sh install.sh --uninstall    # remove the program and service; keeps your settings
#
# Environment:
#   PYTHON       Python 3.11+ to use (default: python3)
#   RWR_SOURCE   what to install: a release wheel URL (default), a wheel file,
#                or a source folder
#   RWR_PREFIX   program folder (default: ~/.local/opt/radio-web-remote)
#   RWR_BIN      folder for the command (default: ~/.local/bin)
#
# Your settings, accounts and certificates live in the data folder
# (Linux ~/.local/share/radio-web-remote, macOS ~/Library/Application
# Support/radio-web-remote) and are never touched by this script.
set -eu

VERSION="0.1.0"
REPO="https://github.com/kidvelvet/radio-web-remote"
SOURCE="${RWR_SOURCE:-$REPO/releases/download/v$VERSION/radio_web_remote-$VERSION-py3-none-any.whl}"
PREFIX="${RWR_PREFIX:-$HOME/.local/opt/radio-web-remote}"
BIN="${RWR_BIN:-$HOME/.local/bin}"
PY="${PYTHON:-python3}"
LABEL="io.github.kidvelvet.radio-web-remote"
OS="$(uname -s)"

say() { printf '%s\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }

service_files() {
    case "$OS" in
        Linux) printf '%s\n' "$HOME/.config/systemd/user/radio-web-remote.service" ;;
        Darwin) printf '%s\n' "$HOME/Library/LaunchAgents/$LABEL.plist" ;;
    esac
}

remove_service() {
    f="$(service_files)"
    [ -n "$f" ] && [ -f "$f" ] || return 0
    case "$OS" in
        Linux)
            systemctl --user disable --now radio-web-remote.service 2>/dev/null || true
            rm -f "$f"
            systemctl --user daemon-reload 2>/dev/null || true ;;
        Darwin)
            launchctl bootout "gui/$(id -u)" "$f" 2>/dev/null || true
            rm -f "$f" ;;
    esac
    say "Removed the service ($f)."
}

install_service() {
    exe="$PREFIX/venv/bin/radio-web-remote"
    f="$(service_files)"
    [ -n "$f" ] || die "--service is for Linux and macOS; on Windows use install.ps1 -Service"
    mkdir -p "$(dirname "$f")"
    case "$OS" in
        Linux)
            sed "s|@EXE@|$exe|g" "$HERE/systemd/radio-web-remote.service" > "$f" 2>/dev/null \
                || write_systemd_unit "$exe" > "$f"
            systemctl --user daemon-reload
            systemctl --user enable --now radio-web-remote.service
            say "Service: systemctl --user status radio-web-remote"
            say "To keep it running when you are logged out:  sudo loginctl enable-linger $USER" ;;
        Darwin)
            launchctl bootout "gui/$(id -u)" "$f" 2>/dev/null || true
            sed -e "s|@EXE@|$exe|g" -e "s|@LABEL@|$LABEL|g" -e "s|@HOME@|$HOME|g" \
                "$HERE/launchd/radio-web-remote.plist" > "$f" 2>/dev/null \
                || write_launchd_plist "$exe" > "$f"
            launchctl bootstrap "gui/$(id -u)" "$f"
            say "Service: launchctl print gui/$(id -u)/$LABEL"
            say "Log: ~/Library/Logs/radio-web-remote.log"
            say "macOS asks for Microphone access the first time audio starts; allow it,"
            say "or the radio's receive audio is silent (see docs/install-macos.md)." ;;
    esac
}

# Fallbacks when this script runs from a pipe, without the packaging folder.
write_systemd_unit() {
    cat <<EOF
[Unit]
Description=radio-web-remote (browser remote for amateur radio transceivers)
After=network-online.target sound.target

[Service]
ExecStart=$1 serve
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
}

write_launchd_plist() {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$1</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/radio-web-remote.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/radio-web-remote.log</string>
</dict>
</plist>
EOF
}

HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo .)"
WANT_SERVICE=0
for arg in "$@"; do
    case "$arg" in
        --service) WANT_SERVICE=1 ;;
        --uninstall)
            remove_service
            rm -rf "$PREFIX" "$BIN/radio-web-remote"
            say "Removed $PREFIX and $BIN/radio-web-remote. Your settings were kept."
            exit 0 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) die "unknown option $arg" ;;
    esac
done

case "$OS" in Linux|Darwin) ;; *) die "this script is for Linux and macOS; on Windows use install.ps1" ;; esac
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
    || die "Python 3.11 or newer is needed (tried '$PY'). Install it, or set PYTHON=/path/to/python3."
"$PY" -c 'import venv, ensurepip' 2>/dev/null \
    || die "Python's venv module is missing. On Debian or Raspberry Pi OS: sudo apt install python3-venv"

if [ "$OS" = Linux ] && ! { ldconfig -p 2>/dev/null | grep -q libportaudio; }; then
    say "Note: the PortAudio library was not found. Audio needs it:"
    say "  Debian, Ubuntu, Raspberry Pi OS:  sudo apt install libportaudio2"
fi

say "Installing radio-web-remote into $PREFIX"
[ -x "$PREFIX/venv/bin/python" ] || "$PY" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/python" -m pip install --quiet --upgrade pip
"$PREFIX/venv/bin/python" -m pip install --quiet --upgrade "$SOURCE"
mkdir -p "$BIN"
ln -sf "$PREFIX/venv/bin/radio-web-remote" "$BIN/radio-web-remote"
"$BIN/radio-web-remote" --version

case ":$PATH:" in *":$BIN:"*) ;; *) say "Add $BIN to your PATH to run radio-web-remote from any folder." ;; esac

if [ "$OS" = Linux ]; then
    for g in dialout audio; do
        if getent group "$g" >/dev/null 2>&1 && ! id -nG | tr ' ' '\n' | grep -qx "$g"; then
            say "Note: you are not in the '$g' group. Serial ports or audio may be refused:"
            say "  sudo usermod -aG $g $USER   (then log out and back in)"
        fi
    done
fi

[ "$WANT_SERVICE" = 1 ] && install_service

say ""
say "Next:"
say "  radio-web-remote init      # first time: admin account and certificates"
say "  radio-web-remote doctor    # finds the radio and says what to fix"
[ "$WANT_SERVICE" = 1 ] || say "  radio-web-remote serve     # or rerun this script with --service"
