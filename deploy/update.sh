#!/usr/bin/env bash
set -euo pipefail
cd /opt/moodflow
exec python3 -m deploy.update "$@"
