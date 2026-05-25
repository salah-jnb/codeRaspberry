#!/usr/bin/env bash
# Install a PipeWire-native noise-suppression source for KODA.
#
# Originally targeted werman/noise-suppression-for-voice (RNNoise wrapped in a
# LADSPA plugin) but that depends on the JUCE framework which needs X11/GTK
# headers — not acceptable on a headless Pi. We use PipeWire's built-in
# `module-echo-cancel` instead: it ships with `pipewire-audio`, uses the
# WebRTC audio-processing library (same DSP as Chrome / Google Meet) for
# noise suppression, and only needs a small config file to expose a
# virtual source that Vosk + the Azure WAV captures will both read.
#
# Usage:  sudo bash scripts/install_rnnoise.sh
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
fi

# ----------------------------------------------------------------------
# 1. Resolve the user that called sudo (config goes in their home).
# ----------------------------------------------------------------------
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
    echo "Cannot resolve home dir for user '$TARGET_USER'." >&2
    exit 1
fi
echo "→ Installing for user: $TARGET_USER (home: $TARGET_HOME)"

# ----------------------------------------------------------------------
# 2. Make sure PipeWire + webrtc-audio-processing are present.
#    `pipewire-audio` contains module-echo-cancel itself.
#    The webrtc lib package name has changed across Debian releases:
#      bullseye/bookworm: libwebrtc-audio-processing1
#      trixie:            libwebrtc-audio-processing-1-3
#    We try them in order.
# ----------------------------------------------------------------------
apt-get install -y --no-install-recommends pipewire-audio pipewire-alsa wireplumber || true
for pkg in libwebrtc-audio-processing-1-3 libwebrtc-audio-processing-1-0 libwebrtc-audio-processing1; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then
        if dpkg -l "$pkg" 2>/dev/null | grep -q '^ii'; then
            echo "→ $pkg already installed"
        else
            apt-get install -y --no-install-recommends "$pkg"
        fi
        break
    fi
done

# Sanity check: verify the SPA AEC backend libspa-aec-webrtc.so exists.
if ! find /usr/lib /usr/local/lib -name 'libspa-aec-webrtc*.so*' 2>/dev/null | grep -q .; then
    echo "⚠️  libspa-aec-webrtc not found on this system." >&2
    echo "    On older Debian try: sudo apt install pipewire-module-echo-cancel" >&2
    echo "    Continuing anyway — module-echo-cancel may still work." >&2
fi

# ----------------------------------------------------------------------
# 3. PipeWire filter config — creates a virtual source "noise_suppressed_source"
#    that picks up the current default mic and applies WebRTC noise suppression.
# ----------------------------------------------------------------------
CONF_DIR="$TARGET_HOME/.config/pipewire/pipewire.conf.d"
CONF_FILE="$CONF_DIR/99-noise-suppression.conf"

sudo -u "$TARGET_USER" mkdir -p "$CONF_DIR"

# We disable echo cancellation + gain control + voice detection — only the
# noise suppression part is desired. The capture happens on whatever PipeWire
# considers the current default source (i.e. our ReSpeaker), the cleaned audio
# is then exposed under "source_noise_suppressed" and any arecord -D pipewire
# picks it up if we make it the new default with `wpctl set-default`.
cat > "$CONF_FILE" <<'EOF'
# WebRTC noise suppression for KODA — added by scripts/install_rnnoise.sh
context.modules = [
    {   name = libpipewire-module-echo-cancel
        args = {
            # Internal processing latency (~21 ms @ 48 kHz). Lower values give
            # tighter response but stress the Pi 4 CPU.
            node.latency = 1024/48000
            library.name = aec/libspa-aec-webrtc
            aec.args = {
                # KEEP : noise suppression (the whole reason we're here).
                webrtc.noise_suppression  = true
                webrtc.high_pass_filter   = true
                webrtc.experimental_ns    = true
                # DROP : we don't have a reference loopback, so AEC would
                # mis-fire; AGC fights with the manual PipeWire source volume
                # we already tuned to 1.5. Voice detection is internal to STT.
                webrtc.echo_cancellation  = false
                webrtc.gain_control       = false
                webrtc.voice_detection    = false
                webrtc.extended_filter    = false
                webrtc.delay_agnostic     = true
            }
            capture.props = {
                node.name    = "capture.noise_suppressed_source"
                node.passive = true
                audio.rate   = 48000
                audio.channels = 1
                audio.position = [ MONO ]
            }
            source.props = {
                node.name        = "source_noise_suppressed"
                node.description = "Noise Suppressed Source (WebRTC NS)"
                media.class      = Audio/Source
                audio.rate       = 48000
                audio.channels   = 1
                audio.position   = [ MONO ]
            }
            playback.props = {
                node.name        = "playback.noise_suppressed_dummy"
                node.passive     = true
                media.class      = Audio/Sink
            }
        }
    }
]
EOF
chown "$TARGET_USER:$TARGET_USER" "$CONF_FILE"
echo "→ Wrote $CONF_FILE"

# ----------------------------------------------------------------------
# 4. Restart PipeWire user services so the new module is loaded.
# ----------------------------------------------------------------------
echo "→ Restarting PipeWire for $TARGET_USER…"
sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" \
    systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || true

sleep 2

echo ""
echo "============================================================"
echo " INSTALLATION TERMINEE"
echo "============================================================"
echo ""
echo "Activer la source dénoisée par défaut (à faire UNE fois) :"
echo ""
echo "  wpctl status                              # noter l'ID 'source_noise_suppressed'"
echo "  wpctl set-default <ID>"
echo "  wpctl set-volume <ID> 1.5"
echo ""
echo "Tester :"
echo "  arecord -D pipewire -f S16_LE -r 16000 -c 1 -d 5 /tmp/clean.wav"
echo "  aplay /tmp/clean.wav"
echo ""
echo "Désactiver :"
echo "  rm $CONF_FILE"
echo "  systemctl --user restart pipewire pipewire-pulse wireplumber"
echo ""
