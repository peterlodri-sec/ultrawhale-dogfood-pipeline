#!/usr/bin/env bash
# Pretty-print and colorize log streams
# Usage: ./pretty_logs.sh

tail -f /tmp/worker1.log /tmp/worker2.log /tmp/ralph.log /tmp/upload.log | sed \
  -e 's/==> \/tmp\/worker1.log <==/\x1b[1;34m[WORKER-1]\x1b[0m/g' \
  -e 's/==> \/tmp\/worker2.log <==/\x1b[1;32m[WORKER-2]\x1b[0m/g' \
  -e 's/==> \/tmp\/ralph.log <==/\x1b[1;33m[RALPH]\x1b[0m/g' \
  -e 's/==> \/tmp\/upload.log <==/\x1b[1;36m[UPLOAD]\x1b[0m/g' \
  -e 's/\[INFO\]/\x1b[32m[INFO]\x1b[0m/g' \
  -e 's/\[ERROR\]/\x1b[1;31m[ERROR]\x1b[0m/g' \
  -e 's/\[WARN\]/\x1b[1;33m[WARN]\x1b[0m/g' \
  -e 's/\[START\]/\x1b[1;35m[START]\x1b[0m/g'
