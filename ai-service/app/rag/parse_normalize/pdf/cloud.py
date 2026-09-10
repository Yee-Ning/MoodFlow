"""MinerU Precision API runner for the optional cloud PDF backend.

The runner implements the existing ``MinerURunner`` contract: it materializes
the official result archive in the parser cache and lets ``MinerUAdapter`` map
those files into the shared ``NormalizedDocument`` model.
"""

from __future__ import annotations

import json
import shutil
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import ParseInput


class MinerUCloudConfig(BaseSettings):
    """Environment-backed settings for the MinerU Precision API."""

    model_config = SettingsConfigDict(
        env_prefix="MINERU_",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    cloud_enabled: bool = False
    api_base_url: AnyHttpUrl = "https://mineru.net"
    api_token: SecretStr | None = None
    model_version: Literal["pipeline", "vlm"] = "vlm"
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    upload_timeout_seconds: float = Field(default=300, gt=0, le=1_800)
    poll_interval_seconds: float = Field(default=5, gt=0, le=60)
    poll_timeout_seconds: float = Field(default=1_800, gt=0, le=7_200)
    max_download_bytes: int = Field(default=536_870_912, gt=0)
    max_archive_entries: int = Field(default=20_000, gt=0)
    max_archive_member_bytes: int = Field(default=268_435_456, gt=0)
    max_uncompressed_bytes: int = Field(default=1_073_741_824, gt=0)

    @model_validator(mode="after")
    def require_token_when_enabled(self) -> "MinerUCloudConfig":
        if self.cloud_enabled and (
            self.api_token is None or not self.api_token.get_secret_value().strip()
        ):
            raise ValueError("MINERU_API_TOKEN is required when cloud is enabled")
        return self

    @classmethod
    def from_env_file(cls, env_file: Path) -> "MinerUCloudConfig":
        return cls(_env_file=env_file)  # type: ignore[call-arg]


class MinerUCloudRunner:
    """Upload one local snapshot and cache the completed MinerU result ZIP."""

    version_label = "cloud-api-v4"
    _ACTIVE_STATES = frozenset(
        {"waiting-file", "pending", "running", "converting"}
    )

    def __init__(
        self,
        config: MinerUCloudConfig,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not config.cloud_enabled:
            raise ValueError("MinerU cloud runner requires MINERU_CLOUD_ENABLED=true")
        self.config = config
        self._client = client
        self._sleeper = sleeper
        self._monotonic = monotonic

    def run(
        self, parse_input: ParseInput, output_directory: Path, allow_ocr: bool
    ) -> None:
        owns_client = self._client is None
        client = self._client or httpx.Client(follow_redirects=False)
        try:
            batch_id, upload_url = self._create_upload(client, parse_input)
            self._upload(client, upload_url, parse_input.raw_bytes)
            zip_url = self._wait_for_result(client, batch_id, parse_input)
            archive = self._download(client, zip_url)
            self._extract(
                archive,
                output_directory,
                {
                    "source_id": parse_input.snapshot.source_id,
                    "batch_id": batch_id,
                    "model_version": self.config.model_version,
                    "final_state": "done",
                    "ocr_path_requested": allow_ocr,
                },
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError("mineru_cloud_unexpected_error") from exc
        finally:
            if owns_client:
                client.close()

    def _create_upload(
        self, client: httpx.Client, parse_input: ParseInput
    ) -> tuple[str, str]:
        source_id = parse_input.snapshot.source_id
        response = self._api_request(
            client,
            "POST",
            "/api/v4/file-urls/batch",
            json={
                "files": [{"name": f"{source_id}.pdf", "data_id": source_id}],
                "model_version": self.config.model_version,
            },
        )
        data = self._api_data(response)
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls")
        if (
            not isinstance(batch_id, str)
            or not batch_id.strip()
            or not isinstance(file_urls, list)
            or len(file_urls) != 1
            or not isinstance(file_urls[0], str)
        ):
            raise ParserError("mineru_cloud_create_response_invalid")
        return batch_id, self._https_url(file_urls[0], "upload")

    def _upload(self, client: httpx.Client, url: str, content: bytes) -> None:
        try:
            response = client.put(
                url,
                content=content,
                timeout=self.config.upload_timeout_seconds,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise ParserError("mineru_cloud_upload_timeout") from exc
        except httpx.RequestError as exc:
            raise ParserError("mineru_cloud_upload_network_error") from exc
        if not 200 <= response.status_code < 300:
            raise ParserError(f"mineru_cloud_upload_http_{response.status_code}")

    def _wait_for_result(
        self,
        client: httpx.Client,
        batch_id: str,
        parse_input: ParseInput,
    ) -> str:
        deadline = self._monotonic() + self.config.poll_timeout_seconds
        while self._monotonic() <= deadline:
            response = self._api_request(
                client,
                "GET",
                f"/api/v4/extract-results/batch/{batch_id}",
            )
            data = self._api_data(response)
            result = self._matching_result(
                data.get("extract_result"), parse_input.snapshot.source_id
            )
            if result is None:
                self._sleeper(self.config.poll_interval_seconds)
                continue
            state = str(result.get("state", "")).strip().lower()
            if state == "done":
                zip_url = result.get("full_zip_url")
                if not isinstance(zip_url, str) or not zip_url.strip():
                    raise ParserError("mineru_cloud_result_url_missing")
                return self._https_url(zip_url, "result")
            if state == "failed":
                detail = self._safe_detail(result.get("err_msg"))
                suffix = f":{detail}" if detail else ""
                raise ParserError(f"mineru_cloud_task_failed{suffix}")
            if state not in self._ACTIVE_STATES:
                raise ParserError("mineru_cloud_task_state_invalid")
            self._sleeper(self.config.poll_interval_seconds)
        raise ParserError("mineru_cloud_poll_timeout")

    def _download(self, client: httpx.Client, url: str) -> bytes:
        try:
            with client.stream(
                "GET",
                url,
                timeout=self.config.upload_timeout_seconds,
                follow_redirects=True,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ParserError(
                        f"mineru_cloud_download_http_{response.status_code}"
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.config.max_download_bytes:
                        raise ParserError("mineru_cloud_download_too_large")
                    chunks.append(chunk)
        except ParserError:
            raise
        except httpx.TimeoutException as exc:
            raise ParserError("mineru_cloud_download_timeout") from exc
        except httpx.RequestError as exc:
            raise ParserError("mineru_cloud_download_network_error") from exc
        return b"".join(chunks)

    def _api_request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        token = self.config.api_token
        if token is None:
            raise ParserError("mineru_cloud_token_missing")
        url = f"{str(self.config.api_base_url).rstrip('/')}{path}"
        try:
            response = client.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {token.get_secret_value()}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=json,
                timeout=self.config.request_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ParserError("mineru_cloud_api_timeout") from exc
        except httpx.RequestError as exc:
            raise ParserError("mineru_cloud_api_network_error") from exc
        if response.status_code in {401, 403}:
            raise ParserError("mineru_cloud_auth_failed")
        if response.status_code == 429:
            raise ParserError("mineru_cloud_rate_limited")
        if response.status_code >= 500:
            raise ParserError("mineru_cloud_service_unavailable")
        if not 200 <= response.status_code < 300:
            raise ParserError(f"mineru_cloud_api_http_{response.status_code}")
        return response

    @staticmethod
    def _api_data(response: httpx.Response) -> dict[str, object]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ParserError("mineru_cloud_api_response_invalid") from exc
        if not isinstance(payload, dict):
            raise ParserError("mineru_cloud_api_response_invalid")
        code = payload.get("code")
        if code != 0:
            raise ParserError(f"mineru_cloud_api_error_{code}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ParserError("mineru_cloud_api_response_invalid")
        return data

    @staticmethod
    def _matching_result(value: object, source_id: str) -> dict[str, object] | None:
        if not isinstance(value, list):
            return None
        candidates = [item for item in value if isinstance(item, dict)]
        for item in candidates:
            if item.get("data_id") == source_id:
                return item
        expected_name = f"{source_id}.pdf"
        for item in candidates:
            if item.get("file_name") == expected_name:
                return item
        return candidates[0] if len(candidates) == 1 else None

    def _extract(
        self,
        archive_bytes: bytes,
        output_directory: Path,
        task_metadata: dict[str, object],
    ) -> None:
        output_directory = output_directory.resolve()
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            archive = zipfile.ZipFile(BytesIO(archive_bytes))
        except zipfile.BadZipFile as exc:
            raise ParserError("mineru_cloud_archive_invalid") from exc
        with archive:
            members = archive.infolist()
            self._validate_members(members)
            with tempfile.TemporaryDirectory(
                prefix=f".{output_directory.name}-", dir=output_directory.parent
            ) as temp_dir:
                staging = Path(temp_dir) / "result-cache"
                staging.mkdir()
                for member in members:
                    relative = self._member_path(member.filename)
                    target = staging.joinpath(*relative.parts)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("xb") as destination:
                        shutil.copyfileobj(source, destination)
                (staging / "cloud_task.json").write_text(
                    json.dumps(task_metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                self._publish_cache(staging, output_directory)

    def _validate_members(self, members: list[zipfile.ZipInfo]) -> None:
        if len(members) > self.config.max_archive_entries:
            raise ParserError("mineru_cloud_archive_too_many_entries")
        total = 0
        for member in members:
            self._member_path(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ParserError("mineru_cloud_archive_unsafe")
            if member.file_size > self.config.max_archive_member_bytes:
                raise ParserError("mineru_cloud_archive_member_too_large")
            total += member.file_size
            if total > self.config.max_uncompressed_bytes:
                raise ParserError("mineru_cloud_archive_too_large")

    @staticmethod
    def _member_path(value: str) -> PurePosixPath:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or path.is_absolute()
            or ".." in path.parts
            or (path.parts and ":" in path.parts[0])
        ):
            raise ParserError("mineru_cloud_archive_unsafe")
        return path

    @staticmethod
    def _publish_cache(staging: Path, destination: Path) -> None:
        if not destination.exists():
            staging.rename(destination)
            return
        for source in sorted(item for item in staging.rglob("*") if item.is_file()):
            relative = source.relative_to(staging)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != source.read_bytes():
                    raise ParserError("mineru_cloud_cache_conflict")
                continue
            shutil.copyfile(source, target)

    @staticmethod
    def _https_url(value: str, purpose: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ParserError(f"mineru_cloud_{purpose}_url_invalid")
        return value

    @staticmethod
    def _safe_detail(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split())[:200]
