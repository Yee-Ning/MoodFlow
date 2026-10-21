"""Probe the running local stack without logging account or provider configuration."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


def request(base: str, path: str, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    query = Request(base.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"})
    try:
        # These probes target the local stack, including when Git/downloads use
        # an ambient proxy that cannot route back to the developer's localhost.
        with build_opener(ProxyHandler({})).open(query, timeout=120) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8088")
    parser.add_argument("--chat", action="store_true", help="Also make one real model-backed GeneralChat request")
    args = parser.parse_args(argv)
    for path in ("/healthz", "/", "/api/auth/status"):
        status, _ = request(args.base_url, path)
        if status != 200:
            raise SystemExit(f"Stack check failed: {path} returned HTTP {status}")
    status, _ = request(args.base_url, "/api/ai/foundation", {"message": ""})
    if status != 422:
        raise SystemExit(f"AI validation check failed: expected HTTP 422, got {status}")
    if args.chat:
        status, body = request(args.base_url, "/api/ai/foundation", {
            "message": "请用一句话解释什么是切分音。", "thread_id": "smoke-" + uuid4().hex,
        })
        result = json.loads(body) if status == 200 else {}
        if result.get("status") != "success" or not result.get("message"):
            raise SystemExit(f"GeneralChat check failed: HTTP {status}; inspect ai-service logs")
    print("Local stack checks passed." + (" GeneralChat returned a response." if args.chat else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
