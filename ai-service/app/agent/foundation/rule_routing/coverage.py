import re
from dataclasses import dataclass

from app.agent.foundation.rule_routing.rule_types import RuleMatch
from app.agent.foundation.routing import IntentItem, UnresolvedSegment

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
    r"再(?=(?:帮|请|给|把|告诉|分析|收藏|暂停|播放|继续|恢复|"
    r"切换|切到|换|下一|上一|查看|打开))"
)
_NEGATED_ACTION = re.compile(
    r"^(?:别|不要|不用|无需)[^,，.。!！?？;；]{0,12}"
    r"(?:切歌|换歌|下一首|上一首|暂停|继续播放|播放|收藏|喜欢|"
    r"加入|添加到|搜索|查找|打开歌词|调大|调小|静音|快进|快退)"
)


@dataclass(frozen=True)
class CoverageResult:
    unresolved_text: str
    unresolved_segments: list[UnresolvedSegment]
    ratio: float
    is_fully_covered: bool


def _mark_span(flags: list[bool], start: int, end: int) -> None:
    for index in range(max(0, start), min(len(flags), end)):
        flags[index] = True


def _effective_positions(text: str, *, ignore_negated_action: bool = False) -> set[int]:
    ignored = [False] * len(text)
    # A cancelled action is a constraint on this turn, not a dispatchable
    # intent. Keep standalone negations unresolved for existing LLM handling.
    if ignore_negated_action:
        for start, end in _segment_spans(text):
            if _NEGATED_ACTION.search(text[start:end].strip()):
                _mark_span(ignored, start, end)
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
    matches: list[RuleMatch] | list[IntentItem],
    unresolved_positions: set[int],
) -> list[UnresolvedSegment]:
    fragments: list[UnresolvedSegment] = []
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
                offset = start + text[start:end].index(fragment)
                fragments.append(UnresolvedSegment(
                    text=fragment, start=offset, end=offset + len(fragment),
                    order=len(fragments),
                ))
    return fragments


def calculate_coverage(text: str, matches: list[RuleMatch] | list[IntentItem]) -> CoverageResult:
    effective = _effective_positions(text, ignore_negated_action=bool(matches))
    covered: set[int] = set()
    for match in matches:
        covered.update(index for index in effective if match.start <= index < match.end)

    unresolved_positions = effective - covered
    ratio = len(covered) / len(effective) if effective else 1.0
    fragments = _unresolved_fragments(text, matches, unresolved_positions)
    return CoverageResult(
        unresolved_text="；".join(fragment.text for fragment in fragments),
        unresolved_segments=fragments,
        ratio=ratio,
        is_fully_covered=not unresolved_positions,
    )
