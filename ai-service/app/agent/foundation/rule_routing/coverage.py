import re
from dataclasses import dataclass

from app.agent.foundation.rule_routing.rule_types import RuleMatch

_PUNCTUATION = frozenset(",.!?;:，。！？；：、()（）[]【】{}“”‘’\"'")
_IGNORABLE_TOKENS = (
    "然后",
    "接着",
    "随后",
    "并且",
    "同时",
    "以及",
    "请你",
    "麻烦",
    "帮我",
    "给我",
    "请",
    "一下",
    "好吗",
    "谢谢",
    "吧",
    "呢",
    "呀",
    "啊",
    "哦",
    "嘛",
    "了",
)
_CLAUSE_SEPARATOR = re.compile(
    r"[,，.!。?？!！;；]+|然后|接着|随后|并且|同时|以及|"
    r"再(?=(?:帮|请|给|把|告诉|分析|收藏|暂停|播放|查看|打开))"
)


@dataclass(frozen=True)
class CoverageResult:
    unresolved_text: str
    ratio: float
    is_fully_covered: bool


def _mark_span(flags: list[bool], start: int, end: int) -> None:
    for index in range(max(0, start), min(len(flags), end)):
        flags[index] = True


def _effective_positions(text: str) -> set[int]:
    ignored = [False] * len(text)
    for match in re.finditer(r"\s+", text):
        _mark_span(ignored, *match.span())
    for index, character in enumerate(text):
        if character in _PUNCTUATION:
            ignored[index] = True
    for token in _IGNORABLE_TOKENS:
        for match in re.finditer(re.escape(token), text):
            _mark_span(ignored, *match.span())
    for separator in _CLAUSE_SEPARATOR.finditer(text):
        _mark_span(ignored, *separator.span())
    return {
        index
        for index, character in enumerate(text)
        if not ignored[index] and character.isalnum()
    }


def _trim_fragment(fragment: str) -> str:
    return fragment.strip(" \t\r\n,，.!。?？!！;；:：、")


def _segment_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for separator in _CLAUSE_SEPARATOR.finditer(text):
        if cursor < separator.start():
            spans.append((cursor, separator.start()))
        cursor = separator.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans


def _unresolved_fragments(
    text: str,
    matches: list[RuleMatch],
    unresolved_positions: set[int],
) -> list[str]:
    fragments: list[str] = []
    for segment_start, segment_end in _segment_spans(text):
        boundaries = {segment_start, segment_end}
        for match in matches:
            if match.end <= segment_start or match.start >= segment_end:
                continue
            boundaries.add(max(segment_start, match.start))
            boundaries.add(min(segment_end, match.end))

        points = sorted(boundaries)
        for start, end in zip(points, points[1:]):
            if not any(start <= index < end for index in unresolved_positions):
                continue
            fragment = _trim_fragment(text[start:end])
            if fragment:
                fragments.append(fragment)
    return fragments


def calculate_coverage(text: str, matches: list[RuleMatch]) -> CoverageResult:
    effective = _effective_positions(text)
    covered: set[int] = set()
    for match in matches:
        covered.update(index for index in effective if match.start <= index < match.end)

    unresolved_positions = effective - covered
    ratio = len(covered) / len(effective) if effective else 1.0
    fragments = _unresolved_fragments(text, matches, unresolved_positions)
    return CoverageResult(
        unresolved_text="；".join(fragments),
        ratio=ratio,
        is_fully_covered=not unresolved_positions,
    )
