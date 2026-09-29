#!/usr/bin/env bash
set -euo pipefail
cd /opt/moodflow
test "$(id -u)" -eq 0
test -f .env.production
install -m 0644 deploy/moodflow-backup.service /etc/systemd/system/moodflow-backup.service
install -m 0644 deploy/moodflow-backup.timer /etc/systemd/system/moodflow-backup.timer
systemctl daemon-reload
systemctl enable --now moodflow-backup.timer
systemctl list-timers moodflow-backup.timer --no-pager
