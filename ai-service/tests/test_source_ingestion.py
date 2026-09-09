import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.rag.source_ingestion.fetchers import FETCHERS, ContentTypeMismatchError
from app.rag.source_ingestion.http_client import (
    PolicyAwareHttpClient,
    RawHttpResponse,
    ResponseTooLargeError,
    RobotsDeniedError,
)
from app.rag.source_ingestion.manifest import (
    CandidateManifestError,
    load_candidates,
    update_review_status,
    write_candidates,
)
from app.rag.source_ingestion.models import (
    CandidateSource,
    FileType,
    ReviewStatus,
    SourceNotApprovedError,
)
from app.rag.source_ingestion.service import SourceIngestionService
from app.rag.source_ingestion.snapshot import RawSnapshotStore

AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_MANIFEST = (
    AI_SERVICE_ROOT
    / "data"
    / "source_ingestion"
    / "jay_chou_corpus_v1"
    / "sources_candidates.json"
)
FIXED_TIME = datetime(2026, 9, 9, 8, 30, 0, tzinfo=UTC)


def _candidate(
    source_id: str = "test-source",
    *,
    file_type: FileType = FileType.HTML,
    status: ReviewStatus = ReviewStatus.PENDING,
) -> CandidateSource:
    return CandidateSource(
        source_id=source_id,
        title="Traceable source",
        url=f"https://example.test/{source_id}.{file_type.value}",
        publisher="Test Publisher",
        author=None,
        published_at=None,
        source_type="media_article",
        file_type=file_type,
        artist="周杰伦",
        album=None,
        topics=["作品影响"],
        source_tier=2,
        source_quality="medium",
        reason="Used to verify the ingestion boundary.",
        status=status,
    )


class StubHttpClient:
    def __init__(self, response: RawHttpResponse) -> None:
        self.response = response
        self.urls: list[str] = []

    def fetch(self, url: str) -> RawHttpResponse:
        self.urls.append(url)
        return self.response


class CandidateManifestTests(unittest.TestCase):
    def test_checked_in_manifest_has_20_to_50_pending_sources(self) -> None:
        candidates = load_candidates(CANDIDATE_MANIFEST)

        self.assertGreaterEqual(len(candidates), 20)
        self.assertLessEqual(len(candidates), 50)
        self.assertTrue(all(item.status is ReviewStatus.PENDING for item in candidates))
        self.assertEqual(
            {item.file_type for item in candidates},
            {FileType.HTML, FileType.PDF, FileType.JSON},
        )
        topics = {topic for item in candidates for topic in item.topics}
        self.assertTrue(
            {
                "范特西",
                "八度空间",
                "叶惠美",
                "七里香",
                "十一月的萧邦",
                "重点歌曲创作背景",
                "专辑制作过程",
                "音乐风格演变",
                "中国风",
                "合作关系",
                "歌手/制作人/作词人采访",
                "作品影响",
            }.issubset(topics)
        )

    def test_schema_rejects_unknown_status_and_duplicate_topics(self) -> None:
        payload = _candidate().model_dump(mode="json")
        payload["status"] = "downloaded"
        with self.assertRaises(ValidationError):
            CandidateSource.model_validate(payload)

        payload["status"] = "pending"
        payload["topics"] = ["作品影响", "作品影响"]
        with self.assertRaises(ValidationError):
            CandidateSource.model_validate(payload)

    def test_duplicate_source_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "candidates.json"
            path.write_text(
                json.dumps(
                    [
                        _candidate().model_dump(mode="json"),
                        _candidate().model_dump(mode="json"),
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CandidateManifestError):
                load_candidates(path)

    def test_human_review_updates_only_the_selected_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "candidates.json"
            write_candidates(path, [_candidate("source-one"), _candidate("source-two")])

            reviewed = update_review_status(
                path, "source-two", ReviewStatus.APPROVED
            )
            reloaded = load_candidates(path)

        self.assertEqual(reviewed.status, ReviewStatus.APPROVED)
        self.assertEqual(reloaded[0].status, ReviewStatus.PENDING)
        self.assertEqual(reloaded[1].status, ReviewStatus.APPROVED)


class PolicyAwareHttpClientTests(unittest.TestCase):
    def _client(self, handler, **kwargs):
        raw_client = httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        policy_client = PolicyAwareHttpClient(
            client=raw_client,
            min_interval_seconds=0,
            **kwargs,
        )
        self.addCleanup(raw_client.close)
        return policy_client

    def test_robots_disallow_blocks_source_request(self) -> None:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /blocked",
                headers={"Content-Type": "text/plain"},
            )

        client = self._client(handler)
        with self.assertRaises(RobotsDeniedError):
            client.fetch("https://example.test/blocked/article")

        self.assertEqual(requested, ["https://example.test/robots.txt"])

    def test_allowed_response_is_returned_as_original_bytes(self) -> None:
        original = b"\x00raw\xffbytes"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(
                200,
                content=original,
                headers={"Content-Type": "application/octet-stream"},
            )

        response = self._client(handler).fetch("https://example.test/source.bin")

        self.assertEqual(response.content, original)
        self.assertEqual(response.http_status, 200)

    def test_response_size_limit_is_enforced(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(
                200,
                content=b"too-large",
                headers={"Content-Type": "text/plain"},
            )

        with self.assertRaises(ResponseTooLargeError):
            self._client(handler, max_response_bytes=3).fetch(
                "https://example.test/source.txt"
            )

    def test_redirect_target_must_pass_its_own_robots_policy(self) -> None:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.host == "origin.test" and request.url.path == "/robots.txt":
                return httpx.Response(404)
            if request.url.host == "origin.test":
                return httpx.Response(
                    302,
                    headers={"Location": "https://destination.test/blocked"},
                )
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nDisallow: /blocked",
                    headers={"Content-Type": "text/plain"},
                )
            return httpx.Response(200, content=b"must not be requested")

        with self.assertRaises(RobotsDeniedError):
            self._client(handler).fetch("https://origin.test/start")

        self.assertNotIn("https://destination.test/blocked", requested)

    def test_redirect_without_location_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(302)

        with self.assertRaises(httpx.HTTPStatusError):
            self._client(handler).fetch("https://example.test/source")

    def test_robots_crawl_delay_is_respected(self) -> None:
        now = [0.0]
        sleeps: list[float] = []

        def monotonic() -> float:
            return now[0]

        def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nCrawl-delay: 2\nAllow: /",
                    headers={"Content-Type": "text/plain"},
                )
            return httpx.Response(
                200,
                content=b"raw",
                headers={"Content-Type": "text/plain"},
            )

        raw_client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(raw_client.close)
        client = PolicyAwareHttpClient(
            client=raw_client,
            min_interval_seconds=0.1,
            monotonic=monotonic,
            sleeper=sleeper,
        )

        client.fetch("https://example.test/first")
        client.fetch("https://example.test/second")

        self.assertEqual(sleeps, [2.0, 2.0])

    def test_excessive_crawl_delay_aborts_instead_of_blocking(self) -> None:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                200,
                text="User-agent: *\nCrawl-delay: 120\nAllow: /",
                headers={"Content-Type": "text/plain"},
            )

        with self.assertRaises(RobotsDeniedError):
            self._client(handler).fetch("https://example.test/source")

        self.assertEqual(requested, ["https://example.test/robots.txt"])


class SourceIngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.manifest_path = self.root / "sources_candidates.json"
        self.snapshot_store = RawSnapshotStore(self.root, now=lambda: FIXED_TIME)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _service(
        self,
        candidate: CandidateSource,
        response: RawHttpResponse,
    ) -> tuple[SourceIngestionService, StubHttpClient]:
        write_candidates(self.manifest_path, [candidate])
        client = StubHttpClient(response)
        return (
            SourceIngestionService(self.manifest_path, self.snapshot_store, client),
            client,
        )

    def test_unapproved_source_cannot_reach_http_client(self) -> None:
        for status in (ReviewStatus.PENDING, ReviewStatus.REJECTED):
            with self.subTest(status=status):
                service, client = self._service(
                    _candidate(status=status),
                    RawHttpResponse(b"unused", "text/html", 200),
                )

                with self.assertRaises(SourceNotApprovedError):
                    service.fetch_one("test-source")

                self.assertEqual(client.urls, [])
                self.assertFalse((self.root / "data" / "raw").exists())

    def test_fetcher_and_snapshot_store_also_enforce_approval(self) -> None:
        candidate = _candidate(status=ReviewStatus.PENDING)
        client = StubHttpClient(RawHttpResponse(b"unused", "text/html", 200))

        with self.assertRaises(SourceNotApprovedError):
            FETCHERS[FileType.HTML].fetch(candidate, client)
        with self.assertRaises(SourceNotApprovedError):
            self.snapshot_store.save(
                candidate,
                RawHttpResponse(b"unused", "text/html", 200),
            )

        self.assertEqual(client.urls, [])
        self.assertFalse((self.root / "data" / "raw").exists())

    def test_all_four_fetchers_preserve_bytes_and_append_manifest(self) -> None:
        cases = {
            FileType.HTML: ("text/html; charset=utf-8", b"<html>raw</html>"),
            FileType.PDF: ("application/pdf", b"%PDF-1.7\x00raw"),
            FileType.JSON: ("application/vnd.api+json", b'{"raw":true}'),
            FileType.TXT: ("text/plain; charset=utf-8", "原始文本".encode()),
        }

        records = []
        for file_type, (content_type, content) in cases.items():
            candidate = _candidate(
                f"approved-{file_type.value}",
                file_type=file_type,
                status=ReviewStatus.APPROVED,
            )
            write_candidates(self.manifest_path, [candidate])
            client = StubHttpClient(RawHttpResponse(content, content_type, 200))
            service = SourceIngestionService(
                self.manifest_path, self.snapshot_store, client
            )

            record = service.fetch_one(candidate.source_id)
            records.append(record)
            self.assertEqual((self.root / record.raw_path).read_bytes(), content)
            self.assertIn(f"data/raw/{file_type.value}/", record.raw_path)
            self.assertTrue(record.content_hash.startswith("sha256:"))

        manifest_lines = self.snapshot_store.manifest_path.read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(manifest_lines), 4)
        self.assertEqual(
            [json.loads(line)["source_id"] for line in manifest_lines],
            [record.source_id for record in records],
        )

    def test_snapshot_collision_never_overwrites_original(self) -> None:
        candidate = _candidate(status=ReviewStatus.APPROVED)
        response = RawHttpResponse(b"immutable", "text/html", 200)
        service, _ = self._service(candidate, response)

        first = service.fetch_one(candidate.source_id)
        with self.assertRaises(FileExistsError):
            service.fetch_one(candidate.source_id)

        self.assertEqual((self.root / first.raw_path).read_bytes(), b"immutable")
        self.assertEqual(
            len(self.snapshot_store.manifest_path.read_text(encoding="utf-8").splitlines()),
            1,
        )

    def test_content_type_mismatch_creates_no_snapshot(self) -> None:
        candidate = _candidate(
            file_type=FileType.PDF,
            status=ReviewStatus.APPROVED,
        )
        service, _ = self._service(
            candidate,
            RawHttpResponse(b"<html>error</html>", "text/html", 200),
        )

        with self.assertRaises(ContentTypeMismatchError):
            service.fetch_one(candidate.source_id)

        self.assertFalse((self.root / "data" / "raw").exists())


if __name__ == "__main__":
    unittest.main()
