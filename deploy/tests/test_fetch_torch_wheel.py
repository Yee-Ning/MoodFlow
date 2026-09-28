"""The production wheel fetch fails closed before any install step."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from deploy import fetch_torch_wheel as wheel


def index_html(hash_value: str, host: str = "download-r2.pytorch.org") -> str:
    return (
        f'<a href="https://{host}/whl/cpu/{wheel.WHEEL_NAME.replace("+", "%2B")}'
        f'#sha256={hash_value}">wheel</a>'
    )


class FakeResponse(BytesIO):
    def __init__(self, body: bytes, url: str, content_type: str = "application/octet-stream") -> None:
        super().__init__(body)
        self.status = 200
        self.url = url
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}


class FakeOpener:
    def __init__(self, wheel_response: FakeResponse | HTTPError, hash_value: str) -> None:
        self.wheel_response = wheel_response
        self.hash_value = hash_value
        self.calls = 0

    def open(self, request, timeout):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return FakeResponse(index_html(self.hash_value).encode(), wheel.INDEX_URL, "text/html")
        if isinstance(self.wheel_response, HTTPError):
            raise self.wheel_response
        return self.wheel_response


def test_official_link_requires_exact_hash_and_host():
    url = wheel.official_wheel_url(index_html(wheel.EXPECTED_SHA256))
    assert url.endswith("torch-2.14.0%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl")
    with pytest.raises(ValueError, match="SHA256"):
        wheel.official_wheel_url(index_html("0" * 64))
    with pytest.raises(ValueError, match="official"):
        wheel.official_wheel_url(index_html(wheel.EXPECTED_SHA256, "untrusted.example"))


def test_verified_wheel_is_promoted_only_after_matching_hash(tmp_path: Path):
    body = b"PK\x03\x04verified-test-wheel"
    sha = hashlib.sha256(body).hexdigest()
    url = f"https://download-r2.pytorch.org/whl/cpu/{wheel.WHEEL_NAME}"
    opener = FakeOpener(FakeResponse(body, url), sha)
    with patch.object(wheel, "EXPECTED_SHA256", sha), patch.object(wheel, "build_opener", return_value=opener):
        result = wheel.fetch(tmp_path)
    assert result["integrity_pass"] is True
    assert (tmp_path / wheel.WHEEL_NAME).read_bytes() == body
    assert not (tmp_path / (wheel.WHEEL_NAME + ".partial")).exists()


def test_wrong_wheel_hash_cannot_be_promoted(tmp_path: Path):
    body = b"PK\x03\x04corrupted-test-wheel"
    url = f"https://download-r2.pytorch.org/whl/cpu/{wheel.WHEEL_NAME}"
    opener = FakeOpener(FakeResponse(body, url), wheel.EXPECTED_SHA256)
    with patch.object(wheel, "build_opener", return_value=opener):
        result = wheel.fetch(tmp_path)
    assert result["integrity_error"] == "wheel SHA256 mismatch"
    assert not (tmp_path / wheel.WHEEL_NAME).exists()
    assert (tmp_path / (wheel.WHEEL_NAME + ".partial")).read_bytes() == body


def test_http_error_is_reported_without_promoting_body(tmp_path: Path):
    url = f"https://download-r2.pytorch.org/whl/cpu/{wheel.WHEEL_NAME}"
    error = HTTPError(url, 403, "Forbidden", {"Content-Type": "text/plain", "Content-Length": "17"}, BytesIO(b"blocked-by-proxy!"))
    opener = FakeOpener(error, wheel.EXPECTED_SHA256)
    with patch.object(wheel, "build_opener", return_value=opener):
        result = wheel.fetch(tmp_path)
    assert result["http_status"] == 403
    assert result["actual_size"] == 17
    assert not (tmp_path / wheel.WHEEL_NAME).exists()


def test_diagnostic_urls_strip_credentials_and_queries():
    assert wheel.public_url("https://user:secret@download-r2.pytorch.org/path?token=secret") == (
        "https://download-r2.pytorch.org/path"
    )
