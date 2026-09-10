"""Deterministic, fail-closed quality filtering for discovered sources."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from .models import SourceQuality
from .v2_models import DiscoveredCandidate, FilterDecision


CORE_TOPICS = frozenset(
    {
        "范特西",
        "八度空间",
        "叶惠美",
        "七里香",
        "十一月的萧邦",
        "创作背景",
        "重点歌曲创作背景",
        "制作过程",
        "专辑制作过程",
        "音乐风格",
        "音乐风格演变",
        "中国风",
        "合作关系",
        "歌手/制作人/作词人采访",
        "专辑背景",
        "歌曲幕后",
        "作品影响",
        "音乐史 / 时代背景",
        "专业乐评",
    }
)

_FORUM_HOSTS = frozenset(
    {"reddit.com", "tieba.baidu.com", "zhihu.com", "douban.com"}
)
_AI_CONTENT_HOSTS = frozenset(
    {"writesonic.com", "jasper.ai", "copy.ai", "writesonic.ai"}
)


class AutoSourceQualityFilter:
    """Reject sources that cannot safely cross the automatic fetch boundary."""

    def evaluate(self, candidate: DiscoveredCandidate) -> FilterDecision:
        reasons: list[str] = []
        if candidate.source_quality is SourceQuality.LOW:
            reasons.append("low-quality")
        if candidate.source_tier > 3:
            reasons.append("tier-four")
        normalized_artist = candidate.artist.casefold().replace(" ", "")
        if "周杰伦" not in normalized_artist and "jaychou" not in normalized_artist:
            reasons.append("wrong-artist")
        if not CORE_TOPICS.intersection(candidate.topics):
            reasons.append("wrong-topic")
        reasons.extend(self._url_reasons(str(candidate.url)))
        return FilterDecision(
            source_id=candidate.source_id,
            accepted=not reasons,
            reasons=list(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _url_reasons(url: str) -> list[str]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        path_parts = {part for part in parsed.path.casefold().split("/") if part}
        query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
        reasons: list[str] = []
        if "search" in path_parts or query_keys.intersection({"q", "query", "search"}):
            reasons.append("search-page")
        if path_parts.intersection({"login", "signin", "sign-in", "auth"}):
            reasons.append("login-page")
        if path_parts.intersection({"ad", "ads", "advert", "advertising"}):
            reasons.append("advertising-page")
        if host in _FORUM_HOSTS or any(host.endswith(f".{item}") for item in _FORUM_HOSTS):
            reasons.append("forum-source")
        if host in _AI_CONTENT_HOSTS or any(
            host.endswith(f".{item}") for item in _AI_CONTENT_HOSTS
        ):
            reasons.append("ai-content-source")
        return reasons
