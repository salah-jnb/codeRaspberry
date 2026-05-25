#!/usr/bin/env bash
# Install the RNNoise LADSPA plugin + a PipeWire filter-chain that exposes
# a denoised virtual source. After install, Vosk (wake-word) AND the WAV
# captures sent to Azure STT both read denoised audio with zero KODA code changes.
#
# Usage:  sudo bash scripts/install_rnnoise.sh
#
# Tested on:
#   - Raspberry Pi OS Bookworm (Debian 12) with PipeWire 0.3.65+
#   - Raspberry Pi 4 (aarch64)
#
set -euo pipefail

# ----------------------------------------------------------------------
# 1. Prerequisites
# ----------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
fi

ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|x86_64) ;;
    armv7l)
        echo "⚠️  ARMv7 (32-bit Pi OS) — build will work but is slower." >&2
        ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 2 ;;
esac

# Pick the original user that ran sudo, so we put the per-user PipeWire
# config in their home (and not in /root/).
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
    echo "Cannot resolve home dir for user '$TARGET_USER'." >&2
    exit 1
fi
echo "→ Installing for user: $TARGET_USER (home: $TARGET_HOME)"

apt-get install -y --no-install-recommends \
    git cmake build-essential autoconf libtool pkg-config \
    pipewire-audio pipewire-alsa wireplumber || true

# ----------------------------------------------------------------------
# 2. Build librnnoise_ladspa.so from source (werman/noise-suppression-for-voice).
#    No prebuilt aarch64 binaries are published, but the build is small (~3-5 min on Pi 4).
# ----------------------------------------------------------------------
LADSPA_DIR=/usr/lib/ladspa
SO_PATH="$LADSPA_DIR/librnnoise_ladspa.so"

mkdir -p "$LADSPA_DIR"

if [[ -f "$SO_PATH" ]]; then
    echo "→ librnnoise_ladspa.so already present at $SO_PATH (skipping build)"
else
    BUILD_DIR=$(mktemp -d)
    echo "→ Cloning werman/noise-suppression-for-voice → $BUILD_DIR"
    git clone --depth=1 --recurse-submodules \
        https://github.com/werman/noise-suppression-for-voice.git "$BUILD_DIR/src"
    pushd "$BUILD_DIR/src" > /dev/null
    mkdir -p build && cd build
    echo "→ Running cmake…"
    cmake -DCMAKE_BUILD_TYPE=Release ..
    echo "→ Compiling (this takes 3-5 minutes on a Pi 4)…"
    make -j"$(nproc)"
    FOUND=$(find . -name "librnnoise_ladspa.so" | head -n1)
    if [[ -z "$FOUND" ]]; then
        echo "ERROR: build produced no librnnoise_ladspa.so" >&2
        exit 3
    fi
    install -m 0644 "$FOUND" "$SO_PATH"
    popd > /dev/null
    rm -rf "$BUILD_DIR"
    echo "→ Installed $SO_PATH ($(stat -c%s "$SO_PATH") bytes)"
fi

# ----------------------------------------------------------------------
# 3. PipeWire filter-chain config (per-user)
# ----------------------------------------------------------------------
CONF_DIR="$TARGET_HOME/.config/pipewire/pipewire.conf.d"
CONF_FILE="$CONF_DIR/99-rnnoise-source.conf"

sudo -u "$TARGET_USER" mkdir -p "$CONF_DIR"

# This config:
#  - reads from the current default mic (capture.props.node.passive=true makes
#    PipeWire auto-link it to whatever input is currently set as default).
#  - applies the RNNoise LADSPA plugin (mono, since Vosk + Azure both want mono).
#  - exposes a new virtual source "rnnoise_source" that any app can pick up.
cat > "$CONF_FILE" <<'EOF'
# RNNoise filter-chain — added by scripts/install_rnnoise.sh
# Edit "VAD Threshold (%)" to tune aggressiveness:
#   30   = light cleanup, keeps soft voices (safer for STT)
#   50   = balanced (default)
#   80   = aggressive — risks chopping the start of words
context.modules = [
    {   name = libpipewire-module-filter-chain
        args = {
            node.description = "Noise Canceling Source (RNNoise)"
            media.name       = "Noise Canceling Source (RNNoise)"
            filter.graph = {
                nodes = [
                    {
                        type   = ladspa
                        name   = rnnoise
                        plugin = /usr/lib/ladspa/librnnoise_ladspa.so
                        label  = noise_suppressor_mono
                        control = {
                            "VAD Threshold (%)"        = 50.0
                            "VAD Grace Period (ms)"    = 200
                            "Retroactive VAD Grace (ms)" = 0
                        }
                    }
                ]
            }
            capture.props = {
                node.name    = "capture.rnnoise_source"
                node.passive = true
                audio.rate   = 48000
                audio.channels = 1
                audio.position = [ MONO ]
            }
            playback.props = {
                node.name    = "rnnoise_source"
                node.description = "rnnoise_source"
                media.class  = Audio/Source
                audio.rate   = 48000
                audio.channels = 1
                audio.position = [ MONO ]
            }
        }
    }
]
EOF
chown "$TARGET_USER:$TARGET_USER" "$CONF_FILE"
echo "→ Wrote $CONF_FILE"

# ----------------------------------------------------------------------
# 4. Restart PipeWire user services
# ----------------------------------------------------------------------
echo "→ Restarting PipeWire for $TARGET_USER…"
sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" \
    systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || true

sleep 2

# ----------------------------------------------------------------------
# 5. Verification
# ----------------------------------------------------------------------
echo ""
echo "============================================================"
echo " INSTALLATION TERMINEE"
echo "============================================================"
echo ""
echo "Pour activer le denoising comme source par defaut (a faire UNE fois,"
echo "se reproduira automatiquement aux reboots ulterieurs) :"
echo ""
echo "  wpctl status                              # noter l'ID de 'rnnoise_source'"
echo "  wpctl set-default <ID>                    # le rendre source par defaut"
echo ""
echo "Test isole apres activation :"
echo "  arecord -D pipewire -f S16_LE -r 16000 -c 1 -d 5 /tmp/denoised.wav"
echo "  aplay /tmp/denoised.wav"
echo ""
echo "Pour A/B comparer (raw vs denoised) :"
echo "  arecord -D pipewire -f S16_LE -r 16000 -c 1 -d 5 /tmp/denoised.wav   # apres set-default"
echo "  wpctl set-default <ID_ReSpeaker_raw>                                 # repasser sur raw"
echo "  arecord -D pipewire -f S16_LE -r 16000 -c 1 -d 5 /tmp/raw.wav"
echo ""
echo "Pour desactiver definitivement :"
echo "  rm $CONF_FILE"
echo "  systemctl --user restart pipewire pipewire-pulse wireplumber"
echo ""
