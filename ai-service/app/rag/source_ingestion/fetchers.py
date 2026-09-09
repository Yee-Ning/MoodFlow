"""Format-specific fetchers that preserve the response body byte-for-byte."""

from abc import ABC, abstractmethod

from .http_client import PolicyAwareHttpClient, RawHttpResponse
from .models import CandidateSource, FileType, ensure_approved


class ContentTypeMismatchError(ValueError):
    """The server returned content incompatible with the approved file type."""


class RawFetcher(ABC):
    file_type: FileType

    def fetch(
        self,
        candidate: CandidateSource,
        client: PolicyAwareHttpClient,
    ) -> RawHttpResponse:
        ensure_approved(candidate)
        response = client.fetch(str(candidate.url))
        if not self.accepts_content_type(response.content_type):
            raise ContentTypeMismatchError(
                f"{candidate.source_id} expected {self.file_type.value}, "
                f"received {response.content_type}"
            )
        return response

    @abstractmethod
    def accepts_content_type(self, content_type: str) -> bool:
        raise NotImplementedError

    @staticmethod
    def media_type(content_type: str) -> str:
        return content_type.partition(";")[0].strip().lower()


class HtmlFetcher(RawFetcher):
    file_type = FileType.HTML

    def accepts_content_type(self, content_type: str) -> bool:
        return self.media_type(content_type) in {
            "text/html",
            "application/xhtml+xml",
            "application/octet-stream",
        }


class PdfFetcher(RawFetcher):
    file_type = FileType.PDF

    def accepts_content_type(self, content_type: str) -> bool:
        return self.media_type(content_type) in {
            "application/pdf",
            "application/octet-stream",
        }


class JsonApiFetcher(RawFetcher):
    file_type = FileType.JSON

    def accepts_content_type(self, content_type: str) -> bool:
        media_type = self.media_type(content_type)
        return (
            media_type in {"application/json", "text/json", "application/octet-stream"}
            or media_type.endswith("+json")
        )


class TextFileFetcher(RawFetcher):
    file_type = FileType.TXT

    def accepts_content_type(self, content_type: str) -> bool:
        media_type = self.media_type(content_type)
        return media_type.startswith("text/") or media_type == "application/octet-stream"


FETCHERS: dict[FileType, RawFetcher] = {
    FileType.HTML: HtmlFetcher(),
    FileType.PDF: PdfFetcher(),
    FileType.JSON: JsonApiFetcher(),
    FileType.TXT: TextFileFetcher(),
}
