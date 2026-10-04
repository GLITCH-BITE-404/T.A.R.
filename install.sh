#!/usr/bin/env bash
# T.A.R. installer -- copies T.A.R. into ~/.config/hypr/scripts/quickshell,
# links the `tar-ai` launcher, and reports which optional tools are missing.
#
#   ./install.sh            install / update
#   ./install.sh --check    only check dependencies
#
# Safe to re-run. It never overwrites files that belong to your Quickshell
# config (MatugenColors.qml, Scaler.qml, WindowRegistry.js) -- the copies in
# compat/ are only used if yours don't exist.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/hypr/scripts/quickshell"
BIN="$HOME/.local/bin"

c_g=$'\e[32m'; c_y=$'\e[33m'; c_r=$'\e[31m'; c_d=$'\e[2m'; c_0=$'\e[0m'
ok()   { printf '%s✓%s %s\n' "$c_g" "$c_0" "$*"; }
warn() { printf '%s!%s %s\n' "$c_y" "$c_0" "$*"; }
die()  { printf '%s✗%s %s\n' "$c_r" "$c_0" "$*" >&2; exit 1; }

check_deps() {
    local missing_req=() missing_opt=()
    for b in qs hyprctl python3 jq; do
        command -v "$b" >/dev/null || missing_req+=("$b")
    done
    # tool -> what it powers
    local opt=(
        "wtype:typing / key presses"       "grim:screenshots"
        "slurp:region select"              "playerctl:media control"
        "pactl:volume"                     "brightnessctl:brightness"
        "nmcli:wifi"                       "bluetoothctl:bluetooth"
        "notify-send:notifications"        "curl:weather / downloads / cloud"
        "trash-put:deleting to the trash"  "fd:file search"
        "qalc:calculator"                  "wf-recorder:screen recording"
        "hyprsunset:night light"           "hyprpicker:colour picker"
        "cliphist:clipboard history"       "tesseract:reading the screen (OCR)"
        "ffmpeg:camera"                    "ollama:local (offline) brain"
    )
    for e in "${opt[@]}"; do
        command -v "${e%%:*}" >/dev/null || missing_opt+=("$e")
    done
    if ((${#missing_req[@]})); then
        die "missing required: ${missing_req[*]}  (install quickshell, hyprland, python, jq)"
    fi
    ok "required tools present (quickshell, hyprland, python3, jq)"
    if ((${#missing_opt[@]})); then
        warn "optional tools missing -- these features won't work until installed:"
        for e in "${missing_opt[@]}"; do
            printf '    %s%-14s%s %s\n' "$c_d" "${e%%:*}" "$c_0" "${e#*:}"
        done
    else
        ok "all optional tools present"
    fi
}

check_deps
[[ "${1:-}" == "--check" ]] && exit 0

mkdir -p "$DEST" "$BIN"

if [[ "$SRC" == "$DEST" ]]; then
    ok "running from the install location -- nothing to copy"
else
    cp -r "$SRC/tar" "$DEST/"
    cp "$SRC"/Tar*.qml "$DEST/"
    ok "copied T.A.R. into $DEST"
fi

for f in MatugenColors.qml Scaler.qml WindowRegistry.js; do
    if [[ -e "$DEST/$f" ]]; then
        printf '  %skept your %s%s\n' "$c_d" "$f" "$c_0"
    else
        cp "$SRC/compat/$f" "$DEST/$f"
        ok "installed fallback $f"
    fi
done

chmod +x "$DEST/tar/bin/tar-ai" "$DEST"/tar/*.py "$DEST/tar/tar_say.sh"
ln -sf "$DEST/tar/bin/tar-ai" "$BIN/tar-ai"
ok "linked $BIN/tar-ai"

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) warn "$BIN is not on your PATH -- add it to run \`tar-ai\`" ;;
esac

cat <<EOF

${c_g}T.A.R. installed.${c_0}
  start it:          tar-ai
  free cloud brain:  get a key at https://aistudio.google.com/apikey
                     then in T.A.R. type:  /key <your-key>   and   /cloud
  all commands:      type /  in T.A.R.
EOF
