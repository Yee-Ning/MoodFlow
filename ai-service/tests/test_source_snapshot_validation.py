import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.rag.source_ingestion.http_client import RawHttpResponse
from app.rag.source_ingestion.snapshot import RawSnapshotStore
from app.rag.source_ingestion.v2_models import DiscoveredCandidate
from app.rag.source_ingestion.validation import (
    InvalidSnapshotError,
    SnapshotValidator,
)


FIXED_TIME = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)


def _candidate(file_type: str, source_id: str = "auto-source") -> DiscoveredCandidate:
    return DiscoveredCandidate(
        source_id=source_id,
        title="周杰伦《范特西》资料",
        url=f"https://example.test/source.{file_type}",
        publisher="Example Publisher",
        author=None,
        published_at=None,
        source_type="academic_paper" if file_type == "pdf" else "media_article",
        file_type=file_type,
        artist="周杰伦",
        album="范特西",
        topics=["专辑背景"],
        source_tier=2,
        source_quality="high",
        reason="Traceable source about Jay Chou and Fantasy.",
        discovery_query="周杰伦 范特西 背景",
        replacement_group="fantasy-background",
        priority=1,
    )


class SnapshotValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = SnapshotValidator()

    def test_valid_html_json_pdf_and_text_are_accepted(self) -> None:
        cases = [
            (
                _candidate("html", "valid-html"),
                RawHttpResponse(
                    "<html><head><title>周杰伦 七里香</title></head>"
                    "<body><article>这是关于七里香创作过程的正式文章。</article></body></html>".encode(),
                    "text/html; charset=utf-8",
                    200,
                ),
            ),
            (
                _candidate("json", "valid-json"),
                RawHttpResponse(
                    json.dumps({"entities": {"Q238819": {"labels": {}}}}).encode(),
                    "application/json",
                    200,
                ),
            ),
            (
                _candidate("pdf", "valid-pdf"),
                RawHttpResponse(
                    b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF",
                    "application/pdf",
                    200,
                ),
            ),
            (
                _candidate("txt", "valid-txt"),
                RawHttpResponse(
                    "周杰伦的音乐风格与时代背景研究。".encode("utf-8"),
                    "text/plain; charset=utf-8",
                    200,
                ),
            ),
        ]

        for candidate, response in cases:
            with self.subTest(file_type=candidate.file_type):
                result = self.validator.validate(candidate, response)
                self.assertTrue(result.valid)
                self.assertEqual(result.reasons, [])
                self.assertEqual(result.size_bytes, len(response.content))

    def test_empty_or_incompatible_content_type_is_rejected(self) -> None:
        empty = self.validator.validate(
            _candidate("html"), RawHttpResponse(b"", "text/html", 200)
        )
        incompatible = self.validator.validate(
            _candidate("pdf"),
            RawHttpResponse(b"%PDF-1.7\n%%EOF", "text/html", 200),
        )

        self.assertIn("empty-content", empty.reasons)
        self.assertIn("content-type-mismatch", incompatible.reasons)

    def test_html_error_login_and_search_pages_are_rejected(self) -> None:
        cases = {
            "html-error-page": b"<html><title>404 Not Found</title><body>missing</body></html>",
            "access-denied-page": b"<html><title>Access Denied</title><body>Forbidden</body></html>",
            "login-page": b"<html><title>Sign in</title><body><input type='password'>Log in to continue</body></html>",
            "search-results-page": b"<html><title>Search results</title><body>Search results for Jay Chou</body></html>",
        }

        for expected_reason, body in cases.items():
            with self.subTest(expected_reason=expected_reason):
                result = self.validator.validate(
                    _candidate("html"), RawHttpResponse(body, "text/html", 200)
                )
                self.assertFalse(result.valid)
                self.assertIn(expected_reason, result.reasons)

    def test_article_with_embedded_site_login_widget_is_not_a_login_page(self) -> None:
        body = (
            "<html><head><title>周杰伦《八度空间》发布现场</title></head><body>"
            "<article><h1>专辑发布现场</h1><p>"
            "这是一篇可公开阅读的完整采访正文，介绍周杰伦在专辑发布现场谈到的创作过程。"
            "正文在登录控件之外仍然完整可见，不应因为评论区登录表单而被拒绝。"
            "</p></article>"
            "<form class='comment-login'><input name='user'>"
            "<input name='password' type='password'></form>"
            "</body></html>"
        ).encode("utf-8")

        result = self.validator.validate(
            _candidate("html"),
            RawHttpResponse(body, "text/html; charset=utf-8", 200),
        )

        self.assertTrue(result.valid, result.reasons)

    def test_json_error_response_and_invalid_json_are_rejected(self) -> None:
        error_result = self.validator.validate(
            _candidate("json"),
            RawHttpResponse(b'{"error":"not found"}', "application/json", 200),
        )
        invalid_result = self.validator.validate(
            _candidate("json"),
            RawHttpResponse(b'{"entities":', "application/json", 200),
        )

        self.assertIn("json-error-response", error_result.reasons)
        self.assertIn("invalid-json", invalid_result.reasons)

    def test_fake_pdf_and_undecodable_text_are_rejected(self) -> None:
        fake_pdf = self.validator.validate(
            _candidate("pdf"),
            RawHttpResponse(b"<html>PDF download failed</html>", "application/pdf", 200),
        )
        bad_text = self.validator.validate(
            _candidate("txt"), RawHttpResponse(b"\x81\x81\x81", "text/plain", 200)
        )

        self.assertIn("invalid-pdf-magic", fake_pdf.reasons)
        self.assertIn("unreadable-text", bad_text.reasons)


class ValidatedSnapshotPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RawSnapshotStore(self.root, now=lambda: FIXED_TIME)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test__invalid_response_never_creates_raw_file_or_success_manifest(self) -> None:
        candidate = _candidate("html")
        response = RawHttpResponse(
            b"<html><title>404 Not Found</title></html>", "text/html", 200
        )
        validation = SnapshotValidator().validate(candidate, response)

        with self.assertRaises(InvalidSnapshotError):
            self.store.save_auto_validated(candidate, response, validation)

        self.assertFalse((self.root / "data/raw").exists())

    def test_valid_auto_candidate_is_saved_without_human_approval(self) -> None:
        candidate = _candidate("json")
        response = RawHttpResponse(
            b'{"entities":{"Q238819":{}}}', "application/json", 200
        )
        validation = SnapshotValidator().validate(candidate, response)

        record = self.store.save_auto_validated(candidate, response, validation)

        self.assertEqual((self.root / record.raw_path).read_bytes(), response.content)
        lines = self.store.manifest_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["source_id"], candidate.source_id)


if __name__ == "__main__":
    unittest.main()
