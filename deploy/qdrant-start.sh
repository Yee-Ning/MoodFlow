#!/bin/sh
set -eu
QDRANT__SERVICE__API_KEY="$(cat /run/secrets/qdrant_api_key)"
test -n "$QDRANT__SERVICE__API_KEY"
export QDRANT__SERVICE__API_KEY
exec /qdrant/entrypoint.sh
