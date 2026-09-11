import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import DetectedFileType
from app.rag.parse_normalize.pdf.adapters import MinerUAdapter
from app.rag.parse_normalize.pdf.cloud import (
    MinerUCloudConfig,
    MinerUCloudRunner,
    PublicDnsHttpsDownloader,
)
from app.rag.parse_normalize.pdf.inspector import PDFInspector
from app.rag.parse_normalize.pdf.models import PDFBackend
from app.rag.parse_normalize.service import create_default_service
from tests.test_pdf_optional_adapters import FIXTURE_ROOT, parse_input_for


FIXTURE_BACKEND = FIXTURE_ROOT / "backends" / "mineru"


def mineru_archive(*, unsafe_name: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("result/full.md", "# Cloud Result\n\nParsed body.\n")
        archive.writestr(
            "result/demo_content_list.json",
            json.dumps(
                [
                    {
                        "type": "title",
                        "text": "Cloud Result",
                        "text_level": 1,
                        "page_idx": 0,
                    },
                    {
                        "type": "text",
                        "text": "Parsed body.",
                        "page_idx": 0,
                    },
                ]
            ),
        )
        archive.writestr("result/demo_middle.json", '{"pdf_info": []}')
        archive.writestr("result/images/figure.png", b"image-bytes")
        if unsafe_name is not None:
            archive.writestr(unsafe_name, b"must-not-escape")
    return buffer.getvalue()


class MinerUCloudConfigTests(unittest.TestCase):
    def test_enabled_cloud_config_requires_a_token(self) -> None:
        with self.assertRaises(ValidationError):
            MinerUCloudConfig(cloud_enabled=True, api_token=None)

    def test_env_file_loads_cloud_settings_without_exposing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "MINERU_CLOUD_ENABLED=true\n"
                "MINERU_API_BASE_URL=https://mineru.net\n"
                "MINERU_API_TOKEN=secret-token\n"
                "MINERU_MODEL_VERSION=vlm\n",
                encoding="utf-8",
            )

            config = MinerUCloudConfig.from_env_file(env_path)

            self.assertTrue(config.cloud_enabled)
            self.assertEqual(config.model_version, "vlm")
            self.assertEqual(config.api_token.get_secret_value(), "secret-token")
            self.assertNotIn("secret-token", repr(config))

    def test_default_service_selects_cloud_runner_from_project_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "MINERU_CLOUD_ENABLED=true\n"
                "MINERU_API_BASE_URL=https://mineru.net\n"
                "MINERU_API_TOKEN=secret-token\n"
                "MINERU_MODEL_VERSION=vlm\n",
                encoding="utf-8",
            )

            service = create_default_service(root)
            parser = service.router.registry.resolve(DetectedFileType.PDF)
            runner = parser.adapters[PDFBackend.MINERU].runner

            self.assertIsInstance(runner, MinerUCloudRunner)


class OfficialArchiveCompatibilityTests(unittest.TestCase):
    def test_adapter_reads_prefixed_official_structured_output_names(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-cloud-shape")

        class OfficialShapeRunner:
            version_label = "cloud-api-v4"

            def run(self, parse_input, output_directory: Path, allow_ocr: bool) -> None:
                source = FIXTURE_BACKEND
                result = output_directory / "nested"
                result.mkdir(parents=True)
                shutil.copy(source / "content_list.json", result / "doc_content_list.json")
                shutil.copy(source / "middle.json", result / "doc_middle.json")
                shutil.copy(source / "document.md", result / "full.md")
                image = result / "images" / "layout.png"
                image.parent.mkdir()
                image.write_bytes(b"fixture-image")

        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MinerUAdapter(
                Path(temp_dir),
                runner=OfficialShapeRunner(),
            )
            output = adapter.parse(
                parse_input,
                PDFInspector().inspect(parse_input.raw_bytes),
            )

            content_list = output.result.metadata["mineru"]["content_list"]
            self.assertEqual(len(content_list), 5)
            self.assertEqual(output.parser_version, "cloud-api-v4+a1.1.0")


class MinerUCloudRunnerTests(unittest.TestCase):
    def test_upload_poll_and_download_preserve_auth_boundary_and_outputs(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-cloud")
        archive_bytes = mineru_archive()
        polls = 0
        requests_seen: list[tuple[str, str, bool]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal polls
            has_auth = "authorization" in request.headers
            requests_seen.append((request.method, str(request.url), has_auth))
            if request.url.path == "/api/v4/file-urls/batch":
                payload = json.loads(request.content)
                self.assertEqual(
                    payload,
                    {
                        "files": [
                            {"name": "pdf-cloud.pdf", "data_id": "pdf-cloud"}
                        ],
                        "model_version": "vlm",
                    },
                )
                self.assertEqual(
                    request.headers["authorization"], "Bearer secret-token"
                )
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "batch_id": "batch-1",
                            "file_urls": ["https://upload.example/signed"],
                        },
                    },
                )
            if request.url.host == "upload.example":
                self.assertNotIn("authorization", request.headers)
                self.assertEqual(request.content, parse_input.raw_bytes)
                return httpx.Response(200)
            if request.url.path == "/api/v4/extract-results/batch/batch-1":
                polls += 1
                state = "running" if polls == 1 else "done"
                result: dict[str, object] = {
                    "file_name": "pdf-cloud.pdf",
                    "data_id": "pdf-cloud",
                    "state": state,
                    "err_msg": "",
                }
                if state == "done":
                    result["full_zip_url"] = "https://cdn.example/result.zip"
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "batch_id": "batch-1",
                            "extract_result": [result],
                        },
                    },
                )
            if request.url.host == "cdn.example":
                self.assertNotIn("authorization", request.headers)
                return httpx.Response(
                    200,
                    content=archive_bytes,
                    headers={"content-type": "application/zip"},
                )
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        config = MinerUCloudConfig(
            cloud_enabled=True,
            api_token="secret-token",
            poll_interval_seconds=0.001,
            poll_timeout_seconds=1,
        )
        runner = MinerUCloudRunner(config, client=client, sleeper=lambda _: None)

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "cache"
            runner.run(parse_input, output, allow_ocr=False)

            self.assertEqual(
                (output / "result" / "full.md").read_text(),
                "# Cloud Result\n\nParsed body.\n",
            )
            self.assertTrue(
                (output / "result" / "demo_content_list.json").is_file()
            )
            task = json.loads((output / "cloud_task.json").read_text())
            self.assertEqual(task["batch_id"], "batch-1")
            self.assertEqual(task["model_version"], "vlm")
            self.assertNotIn("secret-token", json.dumps(task))

        self.assertEqual(polls, 2)
        self.assertEqual(
            [entry[2] for entry in requests_seen], [True, False, True, True, False]
        )

    def test_failed_remote_task_becomes_a_bounded_parser_error(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-cloud-failed")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v4/file-urls/batch":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "batch_id": "batch-failed",
                            "file_urls": ["https://upload.example/signed"],
                        },
                    },
                )
            if request.url.host == "upload.example":
                return httpx.Response(200)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "batch_id": "batch-failed",
                        "extract_result": [
                            {
                                "file_name": "pdf-cloud-failed.pdf",
                                "data_id": "pdf-cloud-failed",
                                "state": "failed",
                                "err_msg": "model rejected document",
                            }
                        ],
                    },
                },
            )

        runner = MinerUCloudRunner(
            MinerUCloudConfig(cloud_enabled=True, api_token="secret-token"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleeper=lambda _: None,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ParserError, "mineru_cloud_task_failed"):
                runner.run(parse_input, Path(temp_dir) / "cache", False)

    def test_unsafe_zip_member_is_rejected_without_writing_outside_cache(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-cloud-unsafe")
        archive_bytes = mineru_archive(unsafe_name="../escaped.txt")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v4/file-urls/batch":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "batch_id": "batch-unsafe",
                            "file_urls": ["https://upload.example/signed"],
                        },
                    },
                )
            if request.url.host == "upload.example":
                return httpx.Response(200)
            if request.url.path.startswith("/api/v4/extract-results/batch/"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "batch_id": "batch-unsafe",
                            "extract_result": [
                                {
                                    "file_name": "pdf-cloud-unsafe.pdf",
                                    "data_id": "pdf-cloud-unsafe",
                                    "state": "done",
                                    "err_msg": "",
                                    "full_zip_url": "https://cdn.example/result.zip",
                                }
                            ],
                        },
                    },
                )
            return httpx.Response(200, content=archive_bytes)

        runner = MinerUCloudRunner(
            MinerUCloudConfig(cloud_enabled=True, api_token="secret-token"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleeper=lambda _: None,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ParserError, "mineru_cloud_archive_unsafe"):
                runner.run(parse_input, root / "cache", False)
            self.assertFalse((root / "escaped.txt").exists())

    def test_cdn_connect_error_uses_verified_public_dns_fallback(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-cloud-dns")
        archive_bytes = mineru_archive()
        fallback_calls: list[tuple[str, int, float]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v4/file-urls/batch":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "batch_id": "batch-dns",
                            "file_urls": ["https://upload.example/signed"],
                        },
                    },
                )
            if request.url.host == "upload.example":
                return httpx.Response(200)
            if request.url.path.startswith("/api/v4/extract-results/batch/"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "batch_id": "batch-dns",
                            "extract_result": [
                                {
                                    "file_name": "pdf-cloud-dns.pdf",
                                    "data_id": "pdf-cloud-dns",
                                    "state": "done",
                                    "err_msg": "",
                                    "full_zip_url": (
                                        "https://cdn-mineru.openxlab.org.cn/result.zip"
                                    ),
                                }
                            ],
                        },
                    },
                )
            raise httpx.ConnectError("TLS connection failed", request=request)

        def fallback(url: str, max_bytes: int, timeout: float) -> bytes:
            fallback_calls.append((url, max_bytes, timeout))
            return archive_bytes

        runner = MinerUCloudRunner(
            MinerUCloudConfig(cloud_enabled=True, api_token="secret-token"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleeper=lambda _: None,
            direct_downloader=fallback,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "cache"
            runner.run(parse_input, output, False)
            self.assertTrue((output / "result" / "full.md").is_file())

        self.assertEqual(len(fallback_calls), 1)
        self.assertEqual(
            fallback_calls[0][0],
            "https://cdn-mineru.openxlab.org.cn/result.zip",
        )


class PublicDnsHttpsDownloaderTests(unittest.TestCase):
    def test_download_connects_to_public_ip_with_original_host_and_sni(self) -> None:
        captured: dict[str, object] = {}

        class Response:
            status = 200
            reason = "OK"
            headers = {"Content-Length": "7"}

            def read(self, amount: int) -> bytes:
                chunks = captured.setdefault("chunks", [b"archive", b""])
                return chunks.pop(0)

        class Connection:
            def request(self, method: str, target: str, *, headers) -> None:
                captured["request"] = (method, target, headers)

            def getresponse(self):
                return Response()

            def close(self) -> None:
                captured["closed"] = True

        def connection_factory(host: str, ip: str, port: int, timeout: float):
            captured["connection"] = (host, ip, port, timeout)
            return Connection()

        downloader = PublicDnsHttpsDownloader(
            resolver=lambda host, timeout: ["1.1.1.1"],
            connection_factory=connection_factory,
        )

        result = downloader(
            "https://cdn.example/result.zip?signature=abc",
            100,
            12.0,
        )

        self.assertEqual(result, b"archive")
        self.assertEqual(
            captured["connection"], ("cdn.example", "1.1.1.1", 443, 12.0)
        )
        method, target, headers = captured["request"]
        self.assertEqual(method, "GET")
        self.assertEqual(target, "/result.zip?signature=abc")
        self.assertEqual(headers["Host"], "cdn.example")
        self.assertNotIn("Authorization", headers)
        self.assertTrue(captured["closed"])


if __name__ == "__main__":
    unittest.main()
