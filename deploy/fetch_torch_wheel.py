"""Fetch the frozen Linux CPU Torch wheel from the official PyTorch index."""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import os
import re
from pathlib import Path
import platform
import sys
import time
from http.client import IncompleteRead
from urllib.error import HTTPError
from urllib.parse import unquote, urldefrag, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

INDEX_URL = "https://download.pytorch.org/whl/cpu/torch/"
WHEEL_NAME = "torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl"
EXPECTED_SHA256 = "a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260"
ALLOWED_HOSTS = {"download.pytorch.org", "download-r2.pytorch.org"}
DOWNLOAD_CHUNK_BYTES = 16 * 1024 * 1024


def public_url(url: str) -> str:
    """Do not persist URL credentials or query strings in diagnostics."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"


class WheelLinks(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href and unquote(urlsplit(href).path).endswith("/" + WHEEL_NAME):
                self.links.append(href)


class RecordingRedirects(HTTPRedirectHandler):
    def __init__(self) -> None:
        self.chain: list[dict[str, str | int]] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        self.chain.append({"from": public_url(req.full_url), "status": code, "to": public_url(newurl)})
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("redirect left the official PyTorch HTTPS hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def official_wheel_url(index_html: str) -> str:
    parser = WheelLinks()
    parser.feed(index_html)
    if len(parser.links) != 1:
        raise ValueError(f"expected one exact CPU wheel link, found {len(parser.links)}")
    url = urljoin(INDEX_URL, parser.links[0])
    clean_url, fragment = urldefrag(url)
    parsed = urlsplit(clean_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("wheel link is outside the official PyTorch HTTPS hosts")
    if unquote(parsed.path.rsplit("/", 1)[-1]) != WHEEL_NAME:
        raise ValueError("wheel filename does not match the frozen Linux CPU tag")
    if fragment.lower() != "sha256=" + EXPECTED_SHA256:
        raise ValueError("index SHA256 disagrees with the frozen expected SHA256")
    return clean_url


def download_wheel(opener, url: str, part: Path, headers: dict[str, str]):
    """Resume bounded HTTP ranges after a truncated transfer; never trust partial bytes."""
    part.write_bytes(b"")
    total = None
    failures = 0
    for attempt in range(1, 65):
        offset = part.stat().st_size
        request = Request(url, headers={**headers, "Range": f"bytes={offset}-{offset + DOWNLOAD_CHUNK_BYTES - 1}"})
        try:
            response = opener.open(request, timeout=120)
            with response:
                status = response.status
                final_url = public_url(response.url)
                content_type = response.headers.get("Content-Type", "")
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match:
                        raise ValueError("missing or invalid Content-Range")
                    start, end, size = map(int, match.groups())
                    if start != offset or end < start or end >= size or (total is not None and total != size):
                        raise ValueError("Content-Range does not match the resumed download")
                    total = size
                    expected_end = end + 1
                elif status == 200 and offset == 0:
                    length = response.headers.get("Content-Length")
                    total = int(length) if length is not None else None
                    expected_end = total
                else:
                    raise ValueError("server ignored a resumed range or returned an unexpected HTTP status")
                with part.open("ab") as target:
                    try:
                        while chunk := response.read(1024 * 1024):
                            target.write(chunk)
                    except IncompleteRead as exc:
                        target.write(exc.partial)
            current = part.stat().st_size
            if total is not None and current > total:
                raise ValueError("download exceeds advertised wheel size")
            if total is None or current == total:
                return status, content_type, total, final_url, attempt
            if expected_end is not None and current > expected_end:
                raise ValueError("range response exceeds advertised segment")
            failures = failures + 1 if current == offset else 0
        except HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise
            failures += 1
        except OSError:
            failures += 1
        if failures >= 3:
            raise OSError("wheel download stalled after three attempts")
        time.sleep(.2)
    raise OSError("wheel download exceeded its bounded retry limit")


def fetch(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    redirects = RecordingRedirects()
    opener = build_opener(redirects)
    headers = {"Cache-Control": "no-cache", "Pragma": "no-cache", "Accept-Encoding": "identity"}
    with opener.open(Request(INDEX_URL, headers=headers), timeout=120) as response:
        if response.status != 200:
            raise ValueError(f"index HTTP status {response.status}")
        wheel_url = official_wheel_url(response.read().decode("utf-8"))
    redirects.chain.clear()
    part = output_dir / (WHEEL_NAME + ".partial")
    try:
        status, content_type, content_length, final_url, attempts = download_wheel(opener, wheel_url, part, headers)
    except HTTPError as exc:
        body = exc.read()
        return {
            "index_url": INDEX_URL,
            "wheel_url": public_url(wheel_url),
            "redirect_chain": redirects.chain,
            "final_url": public_url(exc.url),
            "http_status": exc.code,
            "content_type": exc.headers.get("Content-Type", ""),
            "content_length": exc.headers.get("Content-Length"),
            "actual_size": len(body),
            "filename": WHEEL_NAME,
            "expected_sha256": EXPECTED_SHA256,
            "actual_sha256": hashlib.sha256(body).hexdigest(),
            "integrity_error": "wheel request returned HTTP error; body was not installed",
        }
    digest = hashlib.sha256()
    with part.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    size = part.stat().st_size
    actual_sha256 = digest.hexdigest()
    with part.open("rb") as wheel_file:
        zip_header = wheel_file.read(4).hex()
    result: dict[str, object] = {
        "index_url": INDEX_URL,
        "wheel_url": public_url(wheel_url),
        "redirect_chain": redirects.chain,
        "final_url": final_url,
        "http_status": status,
        "content_type": content_type,
        "content_length": content_length,
        "actual_size": size,
        "filename": WHEEL_NAME,
        "expected_sha256": EXPECTED_SHA256,
        "actual_sha256": actual_sha256,
        "zip_header": zip_header,
        "download_attempts": attempts,
    }
    if content_length is not None and size != int(content_length):
        result["integrity_error"] = "HTTP Content-Length mismatch"
    elif status not in {200, 206} or actual_sha256 != EXPECTED_SHA256:
        result["integrity_error"] = "wheel SHA256 mismatch"
    elif zip_header != "504b0304":
        result["integrity_error"] = "not a ZIP wheel"
    elif urlsplit(final_url).hostname not in ALLOWED_HOSTS or not final_url.startswith("https://"):
        result["integrity_error"] = "final URL left the official PyTorch HTTPS hosts"
    else:
        part.replace(output_dir / WHEEL_NAME)
        result["integrity_pass"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = fetch(args.output_dir)
    except HTTPError as exc:
        result = {
            "integrity_pass": False,
            "error_type": "HTTPError",
            "http_status": exc.code,
            "failed_url": public_url(exc.url),
            "content_type": exc.headers.get("Content-Type", ""),
            "content_length": exc.headers.get("Content-Length"),
        }
    except Exception as exc:
        result = {"integrity_pass": False, "error_type": type(exc).__name__}
    result["python_version"] = sys.version.split()[0]
    result["platform"] = sys.platform
    result["machine"] = platform.machine()
    result["proxy_env_present"] = {
        name: bool(os.environ.get(name)) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result.get("integrity_pass") else 1


if __name__ == "__main__":
    sys.exit(main())
