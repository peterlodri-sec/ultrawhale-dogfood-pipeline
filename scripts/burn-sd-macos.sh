#!/usr/bin/env bash
# macOS SD-card burner for Raspberry Pi images.

set -euo pipefail

IMAGE_SOURCE=""
IMAGE_URL=""
SHA256_EXPECTED=""
EXTRACT_SHA256_EXPECTED=""
SD_DEVICE=""
DRY_RUN=0
KEEP_IMAGE=0
RPI_LITE_LATEST=0
RPI_ARCH="arm64"
PI_ETHERNET_TWEAKS=0
PI_USER="${PI_USER:-ultrawhale}"
PI_HOSTNAME="${PI_HOSTNAME:-ultrawhale-pi}"
SSH_PUBKEY_PATH="${SSH_PUBKEY_PATH:-$HOME/.ssh/id_ed25519.pub}"
WORK_DIR=""
IMAGE_FILE=""
BOOT_MOUNT=""

usage() {
    cat <<'EOF'
Usage:
  scripts/burn-sd-macos.sh --image path/to/os.img[.xz|.zip]
  scripts/burn-sd-macos.sh --url https://example/os.img.xz [--sha256 HEX_OR_SHA256_URL]
  scripts/burn-sd-macos.sh --rpi-lite-latest --pi-ethernet-tweaks

Options:
  --image PATH      Local .img, .img.xz, or .zip containing one .img.
  --url URL         Download image before burning.
  --sha256 VALUE    Expected SHA256 hex, or a URL/file containing '<hex>  filename'.
  --rpi-lite-latest Download latest Raspberry Pi OS Lite from the official Imager catalog.
  --rpi-arch ARCH    Raspberry Pi OS Lite arch: arm64 or armhf. Default: arm64.
  --pi-ethernet-tweaks
                  After writing, enable SSH/cloud-init user, disable WiFi/Bluetooth,
                  and configure Ethernet DHCP for direct router connection.
  --pi-user NAME    First-boot cloud-init user. Default: ultrawhale.
  --pi-hostname NAME
                  First-boot hostname. Default: ultrawhale-pi.
  --ssh-pubkey PATH SSH public key for first login. Default: ~/.ssh/id_ed25519.pub.
  --device DISK     Use a specific whole disk, e.g. /dev/disk4.
  --dry-run         Show detected disk and write commands without writing.
  --keep-image      Keep downloaded/decompressed temporary image.
  -h, --help        Show this help.

Safety:
  The script only runs on macOS, detects external physical disks with diskutil,
  rejects likely system disks, asks for typed confirmation, writes via rdisk,
  syncs, and ejects the card.
EOF
}

log() {
    printf '[burn-sd] %s\n' "$*"
}

die() {
    printf '[burn-sd] ERROR: %s\n' "$*" >&2
    exit 1
}

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '[burn-sd] dry-run:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

cleanup() {
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" && "$KEEP_IMAGE" -eq 0 ]]; then
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --image)
                IMAGE_SOURCE="${2:-}"
                shift 2
                ;;
            --url)
                IMAGE_URL="${2:-}"
                shift 2
                ;;
            --sha256)
                SHA256_EXPECTED="${2:-}"
                shift 2
                ;;
            --rpi-lite-latest)
                RPI_LITE_LATEST=1
                shift
                ;;
            --rpi-arch)
                RPI_ARCH="${2:-}"
                shift 2
                ;;
            --pi-ethernet-tweaks)
                PI_ETHERNET_TWEAKS=1
                shift
                ;;
            --pi-user)
                PI_USER="${2:-}"
                shift 2
                ;;
            --pi-hostname)
                PI_HOSTNAME="${2:-}"
                shift 2
                ;;
            --ssh-pubkey)
                SSH_PUBKEY_PATH="${2:-}"
                shift 2
                ;;
            --device)
                SD_DEVICE="${2:-}"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            --keep-image)
                KEEP_IMAGE=1
                shift
                ;;
            -h | --help)
                usage
                exit 0
                ;;
            *)
                die "Unknown argument: $1"
                ;;
        esac
    done

    if [[ "$RPI_LITE_LATEST" -eq 1 && ( -n "$IMAGE_SOURCE" || -n "$IMAGE_URL" ) ]]; then
        die "Use --rpi-lite-latest, --image, or --url, not more than one."
    fi
    if [[ -n "$IMAGE_SOURCE" && -n "$IMAGE_URL" ]]; then
        die "Use --image or --url, not both."
    fi
    if [[ -z "$IMAGE_SOURCE" && -z "$IMAGE_URL" && "$RPI_LITE_LATEST" -eq 0 ]]; then
        die "Provide --image PATH or --url URL."
    fi
    if [[ "$RPI_ARCH" != "arm64" && "$RPI_ARCH" != "armhf" ]]; then
        die "--rpi-arch must be arm64 or armhf."
    fi
}

check_macos() {
    [[ "$(uname -s)" == "Darwin" ]] || die "This burner is macOS-only."
    command -v diskutil >/dev/null 2>&1 || die "diskutil is required."
    command -v shasum >/dev/null 2>&1 || die "shasum is required."
}

prepare_work_dir() {
    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ultrawhale-sd.XXXXXX")"
}

download_image() {
    local output
    output="$WORK_DIR/$(basename "${IMAGE_URL%%\?*}")"
    [[ "$output" != "$WORK_DIR/" ]] || output="$WORK_DIR/image-download"

    log "Downloading image: $IMAGE_URL"
    run curl -L --fail --progress-bar -o "$output" "$IMAGE_URL"
    IMAGE_SOURCE="$output"
}

select_latest_rpi_lite() {
    local catalog target result
    catalog="https://downloads.raspberrypi.com/os_list_imagingutility_v4.json"
    target="Raspberry Pi OS Lite (64-bit)"
    if [[ "$RPI_ARCH" == "armhf" ]]; then
        target="Raspberry Pi OS Lite (32-bit)"
    fi

    log "Resolving latest ${target} from Raspberry Pi Imager catalog."
    result="$(
        RPI_TARGET="$target" python3 <<'PY'
import json
import os
import urllib.request

catalog = "https://downloads.raspberrypi.com/os_list_imagingutility_v4.json"
target = os.environ["RPI_TARGET"]

def walk(items):
    for item in items:
        if item.get("name") == target and item.get("url"):
            print("\t".join([
                item["url"],
                item.get("extract_sha256", ""),
                item.get("release_date", ""),
            ]))
            return True
        if walk(item.get("subitems", [])):
            return True
    return False

with urllib.request.urlopen(catalog, timeout=30) as response:
    data = json.load(response)

if not walk(data.get("os_list", [])):
    raise SystemExit(f"Could not find {target}")
PY
    )"

    IFS=$'\t' read -r IMAGE_URL EXTRACT_SHA256_EXPECTED release_date <<<"$result"
    log "Latest ${target}: ${release_date} ${IMAGE_URL}"
}

resolve_sha256() {
    local value="$1"
    if [[ "$value" =~ ^https?:// ]]; then
        curl -fsSL "$value" | awk '{print $1; exit}'
    elif [[ -f "$value" ]]; then
        awk '{print $1; exit}' "$value"
    else
        printf '%s\n' "$value"
    fi
}

verify_sha256() {
    [[ -n "$SHA256_EXPECTED" ]] || return 0

    local expected actual
    expected="$(resolve_sha256 "$SHA256_EXPECTED")"
    actual="$(shasum -a 256 "$IMAGE_SOURCE" | awk '{print $1}')"

    [[ "$expected" == "$actual" ]] || die "SHA256 mismatch for $IMAGE_SOURCE"
    log "SHA256 verified."
}

verify_extract_sha256() {
    [[ -n "$EXTRACT_SHA256_EXPECTED" ]] || return 0

    local actual
    actual="$(shasum -a 256 "$IMAGE_FILE" | awk '{print $1}')"

    [[ "$EXTRACT_SHA256_EXPECTED" == "$actual" ]] || die "Extracted image SHA256 mismatch for $IMAGE_FILE"
    log "Extracted image SHA256 verified."
}

prepare_image() {
    if [[ "$RPI_LITE_LATEST" -eq 1 ]]; then
        select_latest_rpi_lite
    fi

    if [[ -n "$IMAGE_URL" ]]; then
        download_image
    fi

    [[ -f "$IMAGE_SOURCE" ]] || die "Image not found: $IMAGE_SOURCE"
    verify_sha256

    case "$IMAGE_SOURCE" in
        *.img)
            IMAGE_FILE="$IMAGE_SOURCE"
            ;;
        *.img.xz | *.xz)
            command -v xz >/dev/null 2>&1 || die "xz is required for .xz images. Install with: brew install xz"
            IMAGE_FILE="$WORK_DIR/$(basename "$IMAGE_SOURCE" .xz)"
            log "Decompressing xz image."
            run xz -dkc "$IMAGE_SOURCE" >"$IMAGE_FILE"
            ;;
        *.zip)
            command -v unzip >/dev/null 2>&1 || die "unzip is required."
            local member
            member="$(zipinfo -1 "$IMAGE_SOURCE" | grep -E '\.img$' | head -n1)"
            [[ -n "$member" ]] || die "No .img member found in zip."
            IMAGE_FILE="$WORK_DIR/$(basename "$member")"
            log "Extracting $member from zip."
            run unzip -p "$IMAGE_SOURCE" "$member" >"$IMAGE_FILE"
            ;;
        *)
            die "Unsupported image type. Use .img, .img.xz, .xz, or .zip."
            ;;
    esac

    [[ -s "$IMAGE_FILE" || "$DRY_RUN" -eq 1 ]] || die "Prepared image is empty: $IMAGE_FILE"
    verify_extract_sha256
}

list_external_disks() {
    diskutil list external physical
}

detect_sd_card() {
    local candidates=()
    local disk

    while IFS= read -r disk; do
        candidates+=("$disk")
    done < <(diskutil list external physical | awk '/^\/dev\/disk[0-9]+/ {print $1}')
    while IFS= read -r disk; do
        [[ " ${candidates[*]} " == *" $disk "* ]] || candidates+=("$disk")
    done < <(
        diskutil list | awk '/^\/dev\/disk[0-9]+/ {gsub(":", "", $1); print $1}' |
            while read -r disk; do
                diskutil info "$disk" 2>/dev/null | grep -Eq "Removable Media:.*(Yes|Removable)" && printf '%s\n' "$disk"
            done
    )

    if [[ "${#candidates[@]}" -eq 0 ]]; then
        list_external_disks || true
        die "No external physical disks detected."
    fi

    log "External physical disks:"
    list_external_disks

    if [[ "${#candidates[@]}" -eq 1 ]]; then
        SD_DEVICE="${candidates[0]}"
        return
    fi

    printf '\nSelect SD card whole disk:\n'
    select disk in "${candidates[@]}"; do
        if [[ -n "${disk:-}" ]]; then
            SD_DEVICE="$disk"
            return
        fi
        printf 'Invalid selection.\n'
    done
}

validate_device() {
    [[ "$SD_DEVICE" =~ ^/dev/disk[0-9]+$ ]] || die "Use a whole disk like /dev/disk4, not a partition."

    local disk_num
    disk_num="${SD_DEVICE#/dev/disk}"
    if ((10#$disk_num <= 1)); then
        die "Refusing to write likely system disk: $SD_DEVICE"
    fi

    local info
    info="$(diskutil info "$SD_DEVICE")"
    printf '%s\n' "$info"

    if ! grep -Eq 'External:[[:space:]]+Yes|Removable Media:[[:space:]]+(Yes|Removable)' <<<"$info"; then
        die "Refusing to write disk that is not reported external/removable: $SD_DEVICE"
    fi
    if grep -Eq 'Virtual:[[:space:]]+Yes|Disk Image' <<<"$info"; then
        die "Refusing to write virtual/disk-image device: $SD_DEVICE"
    fi
}

confirm_write() {
    local CONFIRM typed
    CONFIRM="BURN ${SD_DEVICE}"

    cat <<EOF

About to erase and write:
  Image:  ${IMAGE_FILE}
  Device: ${SD_DEVICE}

Everything on ${SD_DEVICE} will be destroyed.
Type exactly this phrase to continue:
  ${CONFIRM}
EOF

    read -r typed
    if [[ "$typed" != "$CONFIRM" ]]; then
        die "Refusing to write without exact confirmation."
    fi
}

write_image() {
    local RAW_DEVICE
    RAW_DEVICE="/dev/r${SD_DEVICE#/dev/}"

    log "Unmounting $SD_DEVICE"
    run diskutil unmountDisk "$SD_DEVICE"

    log "Writing $IMAGE_FILE to $RAW_DEVICE"
    run sudo dd if="$IMAGE_FILE" of="$RAW_DEVICE" bs=4m conv=sync status=progress

    log "Flushing writes"
    run sync
}

mount_boot_partition() {
    local boot_part
    boot_part="${SD_DEVICE}s1"

    log "Mounting boot partition $boot_part"
    run diskutil mount "$boot_part"
    BOOT_MOUNT="$(diskutil info "$boot_part" | awk -F: '/Mount Point/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}')"
    [[ -d "$BOOT_MOUNT" ]] || die "Could not find boot partition mount point for $boot_part"
}

apply_pi_ethernet_tweaks() {
    [[ "$PI_ETHERNET_TWEAKS" -eq 1 ]] || return 0
    [[ -f "$SSH_PUBKEY_PATH" ]] || die "SSH public key not found: $SSH_PUBKEY_PATH"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "dry-run: would mount bootfs and apply Raspberry Pi Ethernet/headless tweaks"
        return 0
    fi

    mount_boot_partition

    log "Applying Raspberry Pi Ethernet/headless tweaks in $BOOT_MOUNT"
    touch "$BOOT_MOUNT/ssh"

    {
        printf '\n# Ultrawhale Ethernet-only profile\n'
        printf 'dtoverlay=disable-wifi\n'
        printf 'dtoverlay=disable-bt\n'
    } >>"$BOOT_MOUNT/config.txt"

    cat >"$BOOT_MOUNT/meta-data" <<EOF
instance-id: ${PI_HOSTNAME}
local-hostname: ${PI_HOSTNAME}
EOF

    cat >"$BOOT_MOUNT/network-config" <<'EOF'
version: 2
ethernets:
  eth0:
    match:
      name: eth*
    dhcp4: true
    optional: true
EOF

    cat >"$BOOT_MOUNT/user-data" <<EOF
#cloud-config
hostname: ${PI_HOSTNAME}
manage_etc_hosts: true
users:
  - default
  - name: ${PI_USER}
    groups: [adm, sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - $(cat "$SSH_PUBKEY_PATH")
ssh_pwauth: false
disable_root: true
package_update: true
packages:
  - curl
  - ca-certificates
  - git
runcmd:
  - [ rfkill, block, wifi ]
  - [ rfkill, block, bluetooth ]
EOF

    run sync
}

eject_device() {
    if [[ "$BOOT_MOUNT" != "" ]]; then
        log "Unmounting boot partition"
        run diskutil unmount "${SD_DEVICE}s1"
    fi

    log "Ejecting $SD_DEVICE"
    run diskutil eject "$SD_DEVICE"
}

main() {
    parse_args "$@"
    check_macos
    prepare_work_dir
    prepare_image

    if [[ -z "$SD_DEVICE" ]]; then
        detect_sd_card
    fi

    validate_device
    confirm_write
    write_image
    apply_pi_ethernet_tweaks
    eject_device

    log "Done. SD card is ready."
}

main "$@"
