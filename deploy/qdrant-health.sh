#!/bin/bash
set -eu
# Never expose the credential in argv. Docker's health timeout bounds socket IO.
exec 3<>/dev/tcp/127.0.0.1/6333
printf 'GET /readyz HTTP/1.1\r\nHost: localhost\r\napi-key: %s\r\nConnection: close\r\n\r\n' \
    "$(cat /run/secrets/qdrant_api_key)" >&3
IFS= read -r status <&3
exec 3<&- 3>&-
case "$status" in
    *" 200 "*) exit 0 ;;
    *) exit 1 ;;
esac
