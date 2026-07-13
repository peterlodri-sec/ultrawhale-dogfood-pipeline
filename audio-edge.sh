#!/usr/bin/env bash
# audio-edge — local CLI for vaked-audio edge streaming
set -euo pipefail

# Resolve symlinks to find the real script location
_script="${BASH_SOURCE[0]}"
while [ -L "$_script" ]; do
    _target="$(readlink "$_script")"
    _script="$(cd "$(dirname "$_script")" && cd "$(dirname "$_target")" && pwd)/$(basename "$_target")"
done
SCRIPT_DIR="$(cd "$(dirname "$_script")" && pwd)"

exec uv run python3 "$SCRIPT_DIR/audio-edge/cli.py" "$@"
