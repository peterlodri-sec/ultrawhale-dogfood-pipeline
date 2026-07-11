#!/bin/bash
# Loop status script for /loop every 2min

cd /Users/peter.lodri/workspace/peterlodri-sec/self-host-llm || exit 1

echo "=== STATUS [$(date '+%H:%M:%S')] ==="
echo "git:  $(git rev-parse --abbrev-ref HEAD) | dirty: $(git status --porcelain | wc -l)"
echo "llm:  $(curl -s http://localhost:8080/health >/dev/null 2>&1 && echo 'UP' || echo 'DOWN')"
echo "feed: $(ls -1t dogfeed_*.jsonl 2>/dev/null | head -1 | xargs -I {} basename {})"
