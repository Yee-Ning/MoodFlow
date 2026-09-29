#!/bin/sh
set -eu

# Only the production IP lineage should reload the MoodFlow edge proxy.
if [ "${RENEWED_LINEAGE:-}" != /etc/letsencrypt/live/47.83.162.191 ]; then
    exit 0
fi

/usr/sbin/nginx -t
/usr/bin/systemctl reload nginx
