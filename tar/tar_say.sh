#!/usr/bin/env bash
# T.A.R. speech-out.
#   tar_say.sh "<text>" [volume 0.0-1.0]
#
# Picks whatever TTS is installed and routes it to the OUTPUT DEVICE the user
# chose in setup (not "whatever is default" -- on this box 3 of the 4 sinks are
# silent HDMI). Prints NO_TTS if nothing can speak, so the UI can say so once.
#
# Two bugs used to live here: it looked for `piper`, but piper-tts-bin installs
# /usr/bin/piper-tts; and it required $TAR_PIPER_VOICE, which nothing ever set,
# so even a correctly installed piper silently fell through to "no TTS".
set -uo pipefail

TEXT="${1:-}"
VOL="${2:-0.7}"
[[ -z "$TEXT" ]] && exit 0

DATA="${TAR_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/bite-os/tar}"
CONF="$DATA/config.json"
VOICE_DIR="$DATA/voices"

jqget() { [[ -r "$CONF" ]] && jq -r "$1 // empty" "$CONF" 2>/dev/null || true; }

VOICE_NAME="$(jqget '.voice')"
SINK="$(jqget '.devices.sink')"
[[ -n "$SINK" ]] && export PULSE_SINK="$SINK"

VOL_PCT=$(awk -v v="$VOL" 'BEGIN{ v=(v<0?0:(v>1?1:v)); printf "%d", v*200 }')

play_raw() {   # stdin: s16 mono 22050
    # pw-play is the libsndfile frontend and REJECTS raw PCM on stdin
    # ("Format not recognised"), exiting non-zero after the pipeline has
    # already succeeded -- which is how this silently produced no sound while
    # looking like it worked. pw-cat --raw is the correct tool.
    if command -v pw-cat >/dev/null 2>&1; then
        pw-cat --playback --raw --rate 22050 --format s16 --channels 1 \
               ${SINK:+--target "$SINK"} --volume "$VOL" - 2>/dev/null
    elif command -v paplay >/dev/null 2>&1; then
        paplay --raw --rate=22050 --format=s16le --channels=1 \
               ${SINK:+--device="$SINK"} 2>/dev/null
    else
        aplay -q -r 22050 -f S16_LE -c 1 2>/dev/null
    fi
}

# ---- piper (neural). Binary is piper-tts on Arch; accept both.
PIPER=""
for c in piper-tts piper; do
    command -v "$c" >/dev/null 2>&1 && { PIPER="$c"; break; }
done
if [[ -n "$PIPER" ]]; then
    MODEL="${TAR_PIPER_VOICE:-}"
    if [[ -z "$MODEL" && -n "$VOICE_NAME" && -f "$VOICE_DIR/$VOICE_NAME.onnx" ]]; then
        MODEL="$VOICE_DIR/$VOICE_NAME.onnx"
    fi
    # last resort: any voice sitting in the voice dir
    if [[ -z "$MODEL" ]]; then
        MODEL="$(find "$VOICE_DIR" -maxdepth 1 -name '*.onnx' 2>/dev/null | head -1)"
    fi
    if [[ -n "$MODEL" && -f "$MODEL" ]]; then
        printf '%s' "$TEXT" \
            | "$PIPER" --model "$MODEL" --output_raw 2>/dev/null \
            | play_raw
        exit 0
    fi
fi

if command -v espeak-ng >/dev/null 2>&1; then
    espeak-ng -a "$VOL_PCT" -s 160 -- "$TEXT" >/dev/null 2>&1
    exit 0
fi

if command -v espeak >/dev/null 2>&1; then
    espeak -a "$VOL_PCT" -s 160 -- "$TEXT" >/dev/null 2>&1
    exit 0
fi

if command -v flite >/dev/null 2>&1; then
    flite -t "$TEXT" >/dev/null 2>&1
    exit 0
fi

echo "NO_TTS"
