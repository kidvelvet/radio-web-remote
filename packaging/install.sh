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
OS="$(uname -s)"

say() { printf '%s\n' "$*"; }
# A path as a literal inside a pgrep pattern (an extended regular expression).
re() { printf '%s' "$1" | sed 's/[][\\.^$*+?(){}|]/\\&/g'; }
# This user's server, started through the venv or the command link in $BIN.
EXE_PATTERN="($(re "$PREFIX")/venv/bin|$(re "$BIN"))/radio-web-remote serve"
LOOP_PATTERN="$(re "$PREFIX")/radio-web-remote[.]command"  # the macOS Terminal restart loop
server_running() { pgrep -u "$(id -u)" -f "$EXE_PATTERN" >/dev/null 2>&1; }
loop_running() { pgrep -u "$(id -u)" -f "$LOOP_PATTERN" >/dev/null 2>&1; }
# Services start in the data folder: a stray ./config.yaml elsewhere must not win.
case "$(uname -s)" in
    Darwin) DATA="$HOME/Library/Application Support/radio-web-remote" ;;
    *) DATA="${XDG_DATA_HOME:-$HOME/.local/share}/radio-web-remote" ;;
esac
die() { printf 'install: %s\n' "$*" >&2; exit 1; }

service_files() {
    case "$OS" in
        Linux) printf '%s\n' "$HOME/.config/systemd/user/radio-web-remote.service" ;;
        Darwin) printf '%s\n' "$PREFIX/radio-web-remote.command" ;;
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
            rm -f "$f"
            say "Also remove radio-web-remote.command from System Settings > General > Login Items." ;;
    esac
    say "Removed the service ($f)."
}

stop_server() {  # before deleting the program it runs from
    case "$OS" in
        Linux) systemctl --user stop radio-web-remote.service 2>/dev/null || true ;;
    esac
    if loop_running; then  # first, or it starts the server again 3 s later
        pkill -u "$(id -u)" -f "$LOOP_PATTERN" || true
    fi
    if server_running; then
        pkill -u "$(id -u)" -f "$EXE_PATTERN" || true
        i=0  # let it finish shutting down (it sends TX off) before files go away
        while server_running && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
        say "Stopped the running server."
    fi
}

install_service() {
    exe="$PREFIX/venv/bin/radio-web-remote"
    f="$(service_files)"
    [ -n "$f" ] || die "--service is for Linux and macOS; on Windows use install.ps1 -Service"
    case "$PREFIX$DATA" in  # systemd reads % and \ inside the unit's quoted paths
        *%*|*\\*) die "--service needs a program and data folder without % or \\ in the path" ;;
    esac
    mkdir -p "$(dirname "$f")"
    case "$OS" in
        Linux)
            mkdir -p "$DATA"
            write_systemd_unit "$exe" > "$f"
            systemctl --user daemon-reload
            systemctl --user enable radio-web-remote.service
            systemctl --user restart radio-web-remote.service  # starts it, or picks up an upgrade
            if [ ! -f "$DATA/users.db" ]; then
                say "After 'radio-web-remote init', start it with:  systemctl --user restart radio-web-remote"
            fi
            say "Service: systemctl --user status radio-web-remote"
            say "To keep it running when you are logged out:  sudo loginctl enable-linger $USER" ;;
        Darwin)
            # Not a LaunchAgent: macOS gives a launchd service silence from every
            # audio input, the radio's codec included, and never asks (tested
            # on macOS 26, docs/install-macos.md). Terminal can hold Microphone
            # permission, so the server runs in a Terminal window started at login.
            write_command "$exe" > "$f"
            chmod +x "$f"
            if loop_running || server_running; then
                say "The server is already running. Close its Terminal window to stop it;"
                say "it starts again with the new version when you open $f."
            else
                open "$f"
                say "Started the server in a Terminal window ($f)."
            fi
            say "If Terminal asks for Microphone access, allow it (needed even to listen)."
            say "To start it at login: System Settings > General > Login Items >"
            say "Open at Login > + and choose $f" ;;
    esac
}

# Kept in step with packaging/systemd/radio-web-remote.service (the copy for
# people who write the unit by hand).
write_systemd_unit() {
    cat <<EOF
[Unit]
Description=radio-web-remote (browser remote for amateur radio transceivers)

[Service]
ExecStart="$1" serve
Environment="RADIO_WEB_REMOTE_HOME=$DATA"
WorkingDirectory=$DATA
Restart=always
RestartSec=3
# 78: setup incomplete (no admin yet, bad config). Restarting cannot help;
# any other exit, a crash included, restarts (the server sends TX off first).
RestartPreventExitStatus=78

[Install]
WantedBy=default.target
EOF
}

write_command() {
    cat <<EOF
#!/bin/sh
# radio-web-remote in a Terminal window (macOS). Restarts the server if it
# stops: a key sent over CI-V survives the server dying, and the server
# sends TX off first thing when it starts. Close this window to stop it.
mkdir -p "$DATA" && cd "$DATA" || exit 1
export RADIO_WEB_REMOTE_HOME="$DATA"
while true; do
    "$1" serve
    echo "radio-web-remote stopped (exit \$?); restarting in 3 s. Close this window to stop."
    sleep 3
done
EOF
}

WANT_SERVICE=0
for arg in "$@"; do
    case "$arg" in
        --service) WANT_SERVICE=1 ;;
        --uninstall)
            stop_server
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

if [ "$OS" = Linux ] && ! "$PY" -c 'import ctypes.util, sys; sys.exit(0 if ctypes.util.find_library("portaudio") else 1)' 2>/dev/null; then
    say "Note: the PortAudio library was not found. Audio needs it:"
    say "  Debian, Ubuntu, Raspberry Pi OS:  sudo apt install libportaudio2"
fi

say "Installing radio-web-remote into $PREFIX"
# A venv made while python3-venv was missing has no pip: make it again.
if ! "$PREFIX/venv/bin/python" -m pip --version >/dev/null 2>&1; then
    rm -rf "$PREFIX/venv"
    "$PY" -m venv "$PREFIX/venv" || die "could not create $PREFIX/venv"
fi
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

if [ "$WANT_SERVICE" = 1 ]; then
    install_service
elif [ "$OS" = Linux ] && systemctl --user is-active --quiet radio-web-remote.service 2>/dev/null; then
    systemctl --user restart radio-web-remote.service
    say "Restarted the running service with the new version."
elif server_running; then
    say "The server is running the old version until you restart it."
fi

say ""
say "Next:"
say "  radio-web-remote init      # first time: admin account and certificates"
say "  radio-web-remote doctor    # finds the radio and says what to fix"
[ "$WANT_SERVICE" = 1 ] || say "  radio-web-remote serve     # or rerun this script with --service"
