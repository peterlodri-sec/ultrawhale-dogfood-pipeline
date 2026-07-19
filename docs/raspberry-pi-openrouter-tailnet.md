# Raspberry Pi 3B+ OpenRouter + Tailnet Setup

This profile runs Ultrawhale on a Raspberry Pi 3B+ with:

- Ethernet-only networking
- OpenRouter `openrouter/auto` as the OpenAI-compatible LLM endpoint
- Tailscale SSH
- A plain-text status/log endpoint bound only to the Pi's Tailscale IPv4 address

## 1. Prepare the Pi

Use 64-bit Raspberry Pi OS Lite. Boot it on Ethernet and clone this repo onto the Pi.

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/peterlodri-sec/ultrawhale-dogfood-pipeline.git
cd ultrawhale-dogfood-pipeline
```

Create a reusable or ephemeral Tailscale auth key in the Tailscale admin console, then run:

```bash
export TAILSCALE_AUTHKEY="tskey-auth-..."
export TAILSCALE_HOSTNAME="ultrawhale-pi"
sudo -E scripts/install-pi-openrouter-tailnet.sh
```

If you skip `TAILSCALE_AUTHKEY`, the installer leaves Tailscale installed and you can authenticate manually:

```bash
sudo tailscale up --ssh --accept-dns=false
```

## 2. Configure Secrets

Edit the root-only env file:

```bash
sudo nano /etc/ultrawhale/openrouter.env
```

Set at least:

```bash
OPENROUTER_API_KEY=sk-or-...
LLM_HOST=https://openrouter.ai/api/v1
LLM_MODEL=openrouter/auto
```

Optional:

```bash
HF_TOKEN=hf_...
ULTRAWHALE_HF_REPO=PeetPedro/ultrawhale-dogfood
ULTRAWHALE_CATEGORY=cs
```

## 3. Start Services

```bash
sudo systemctl restart ultrawhale ultrawhale-tailnet-status
sudo systemctl start ultrawhale-upload.timer
```

Check local service state:

```bash
systemctl status ultrawhale
systemctl status ultrawhale-tailnet-status
```

## 4. Monitor From Tailnet Only

From another tailnet device:

```bash
tailscale status
curl http://<pi-tailscale-ip>:8765/status
tailscale ssh ultrawhale-pi
```

Follow logs over Tailscale SSH:

```bash
sudo journalctl -u ultrawhale -f
sudo journalctl -u ultrawhale-tailnet-status -f
```

The HTTP monitor binds to `tailscale ip -4` by default. It is not exposed on `0.0.0.0` or the Ethernet LAN address unless you explicitly override `ULTRAWHALE_TAILNET_STATUS_HOST`.

## 5. Ethernet-Only Notes

The installer:

- adds `dtoverlay=disable-wifi` and `dtoverlay=disable-bt` to the Pi boot config
- blocks WiFi and Bluetooth with `rfkill`
- blacklists the common Pi 3B+ wireless/Bluetooth kernel modules

Reboot once after installation:

```bash
sudo reboot
```

After reboot:

```bash
rfkill list
tailscale ip -4
curl http://$(tailscale ip -4):8765/status
```
