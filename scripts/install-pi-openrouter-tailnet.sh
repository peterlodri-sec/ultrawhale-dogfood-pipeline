#!/usr/bin/env bash
# Install Ultrawhale on Raspberry Pi OS for Ethernet-only OpenRouter generation and tailnet monitoring.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${ULTRAWHALE_APP_DIR:-/opt/ultrawhale}"
ENV_DIR="/etc/ultrawhale"
ENV_FILE="${ENV_DIR}/openrouter.env"
BOOT_CONFIG="/boot/firmware/config.txt"

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo -E so TAILSCALE_AUTHKEY is preserved when provided."
    exit 1
fi

if [[ ! -f "$BOOT_CONFIG" ]]; then
    BOOT_CONFIG="/boot/config.txt"
fi

log() {
    echo "[ultrawhale-pi] $*"
}

ensure_line() {
    local line="$1"
    local file="$2"
    grep -qxF "$line" "$file" 2>/dev/null || echo "$line" >> "$file"
}

install_packages() {
    log "Installing base packages"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates git rsync rfkill python3 python3-venv

    if ! command -v tailscale >/dev/null 2>&1; then
        log "Installing Tailscale"
        curl -fsSL https://tailscale.com/install.sh | sh
    fi

    if ! command -v uv >/dev/null 2>&1; then
        log "Installing uv"
        curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    fi
}

disable_wireless() {
    log "Configuring Ethernet-only networking"
    if [[ -f "$BOOT_CONFIG" ]]; then
        ensure_line "dtoverlay=disable-wifi" "$BOOT_CONFIG"
        ensure_line "dtoverlay=disable-bt" "$BOOT_CONFIG"
    fi

    rfkill block wifi || true
    rfkill block bluetooth || true

    cat >/etc/modprobe.d/ultrawhale-no-wireless.conf <<'EOF'
blacklist brcmfmac
blacklist brcmutil
blacklist btbcm
blacklist hci_uart
EOF
}

install_app() {
    log "Creating ultrawhale user and app directory"
    id -u ultrawhale >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/ultrawhale --shell /usr/sbin/nologin ultrawhale
    getent group systemd-journal >/dev/null 2>&1 && usermod -a -G systemd-journal ultrawhale
    install -d -o ultrawhale -g ultrawhale "$APP_DIR" /var/lib/ultrawhale /var/log/ultrawhale "$ENV_DIR"

    log "Syncing checkout to ${APP_DIR}"
    rsync -a --delete \
        --exclude ".git" \
        --exclude ".venv" \
        --exclude "dogfeed_parallel" \
        --exclude "ralph_logs" \
        "$REPO_DIR"/ "$APP_DIR"/
    chown -R ultrawhale:ultrawhale "$APP_DIR" /var/lib/ultrawhale /var/log/ultrawhale

    log "Installing Python dependencies"
    sudo -u ultrawhale HOME=/var/lib/ultrawhale bash -lc "cd '$APP_DIR' && /usr/local/bin/uv sync --all-extras"
}

install_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log "Installing env template at ${ENV_FILE}"
        install -m 0600 -o root -g root "$REPO_DIR/deploy/pi-openrouter.env.example" "$ENV_FILE"
    else
        log "Keeping existing ${ENV_FILE}"
    fi
}

install_systemd() {
    log "Installing systemd units"
    install -m 0644 "$REPO_DIR"/deploy/systemd/*.service /etc/systemd/system/
    install -m 0644 "$REPO_DIR"/deploy/systemd/*.timer /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable tailscaled.service ultrawhale.service ultrawhale-tailnet-status.service ultrawhale-upload.timer
}

configure_tailscale() {
    systemctl enable --now tailscaled.service

    if [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
        log "Authenticating Tailscale with provided auth key"
        tailscale up \
            --auth-key="${TAILSCALE_AUTHKEY}" \
            --hostname="${TAILSCALE_HOSTNAME:-ultrawhale-pi}" \
            --ssh \
            --accept-dns=false
    else
        log "TAILSCALE_AUTHKEY not set; run: sudo tailscale up --ssh --accept-dns=false"
    fi
}

main() {
    install_packages
    disable_wireless
    install_app
    install_env
    install_systemd
    configure_tailscale

    log "Edit ${ENV_FILE} and set OPENROUTER_API_KEY."
    log "Then start: sudo systemctl restart ultrawhale ultrawhale-tailnet-status"
    log "Monitor on tailnet: curl http://$(tailscale ip -4 2>/dev/null | head -n1):8765/status"
    log "Reboot to fully apply WiFi/Bluetooth overlays."
}

main "$@"
