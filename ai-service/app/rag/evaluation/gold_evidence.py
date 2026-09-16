"""Build independent human-review evidence from a frozen Source/Parent release.

Only pending query text and immutable Parent content enter candidate selection.
No online traces, rankings, Silver labels, or answer generation are inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from app.rag.evaluation.models import AnnotationStatus, RAGEvaluationCase

RELEASE_ARTIFACT = "knowledge_enriched_chunks.jsonl"
PARENT_LIMIT = 5
MAX_PER_SOURCE = 2
EXCERPT_LENGTH = 850
SPAN_LENGTH = 350
_MATCHABLE = re.compile(r"[\u3400-\u9fff]+|[a-z]+|\d{4}")
_QUOTED_TITLE = re.compile(r"《([^》]{1,40})》")
_EXCLUSION = re.compile(r"不含|不要|排除|不包括")
_COMPARISON = re.compile(r"([\u3400-\u9fff]{2,14})(?:和|与|及)([\u3400-\u9fff]{2,18})")
_FOCUS_STOP = re.compile(r"的|有|合作|音乐|風格|风格|资料|故事|背景|相比|区别|异同|什么")
_ALIASES = {
    "jay chou": "周杰伦",
    "周董": "周杰伦",
    "周杰倫": "周杰伦",
    "林俊傑": "林俊杰",
    "洪敬堯": "洪敬尧",
    "葉惠美": "叶惠美",
    "東風破": "东风破",
    "雙截棍": "双截棍",
    "青花瓷": "青花瓷",
    "音樂": "音乐",
    "風格": "风格",
    "創作": "创作",
    "專輯": "专辑",
    "影響": "影响",
    "編曲": "编曲",
    "採訪": "采访",
    "新聞": "新闻",
    "華語": "华语",
}


@dataclass(frozen=True)
class SourceParent:
    parent_id: str
    doc_id: str
    source_id: str
    source_title: str
    source_name: str
    source_url: str | None
    source_document_path: str
    breadcrumb: str
    content: str


@dataclass(frozen=True)
class EvidenceCandidate:
    parent_id: str
    doc_id: str
    source_id: str
    source_title: str
    source_name: str
    source_url: str | None
    source_document_path: str
    breadcrumb: str
    excerpt: str
    suggested_supporting_span: str


def load_frozen_parents(manifest_path: Path) -> tuple[str, str, list[SourceParent]]:
    """Verify the immutable knowledge release before exposing its Parent text."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    release_id = manifest.get("release_id")
    expected = manifest.get("artifacts", {}).get(RELEASE_ARTIFACT)
    if not isinstance(release_id, str) or not release_id:
        raise ValueError("frozen release has no release_id")
    if not isinstance(expected, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected):
        raise ValueError("frozen release has no valid Parent artifact checksum")
    source_path = manifest_path.parent / RELEASE_ARTIFACT
    digest = hashlib.sha256()
    with source_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    actual = f"sha256:{digest.hexdigest()}"
    if actual != expected:
        raise ValueError("frozen Parent artifact checksum mismatch")

    parents: list[SourceParent] = []
    seen: set[str] = set()
    with source_path.open(encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, start=1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            if not str(row.get("chunk_id", "")).startswith("parent-"):
                continue
            parent = _parent_from_row(row, line_number)
            if parent.parent_id in seen:
                raise ValueError(f"frozen release has duplicate Parent:{parent.parent_id}")
            seen.add(parent.parent_id)
            parents.append(parent)
    if len(parents) < PARENT_LIMIT:
        raise ValueError("frozen release has too few Parents for independent review")
    return release_id, actual, parents


def build_source_candidates(
    cases: Sequence[RAGEvaluationCase],
    parents: Sequence[SourceParent],
) -> dict[str, list[EvidenceCandidate]]:
    """Select five source-diverse lexical candidates per query without labels."""
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("review cases contain duplicate IDs")
    for case in cases:
        if case.annotation_status is not AnnotationStatus.PENDING:
            raise ValueError(f"review case is not pending:{case.case_id}")
        if any((
            case.relevant_parent_ids, case.supporting_spans, case.required_facts,
            case.expected_source_ids, case.reference_answer,
        )):
            raise ValueError(f"review case contains prefilled evidence labels:{case.case_id}")
    if len({parent.parent_id for parent in parents}) != len(parents):
        raise ValueError("source Parents contain duplicate IDs")

    document_terms = [_terms(parent.content + " " + parent.breadcrumb) for parent in parents]
    document_frequency: Counter[str] = Counter()
    for terms in document_terms:
        document_frequency.update(terms.keys())
    average_length = sum(sum(terms.values()) for terms in document_terms) / len(parents)
    results: dict[str, list[EvidenceCandidate]] = {}
    for case in cases:
        positive_query, excluded = _query_constraints(case.query)
        query_terms = _terms(positive_query)
        focus_phrases = _focus_phrases(positive_query)
        scored = [
            (
                _source_score(
                    parent, terms, query_terms, focus_phrases, excluded,
                    document_frequency, len(parents), average_length,
                ),
                parent,
            )
            for parent, terms in zip(parents, document_terms, strict=True)
        ]
        scored.sort(key=lambda item: (-item[0], item[1].source_id, item[1].parent_id))
        selected: list[SourceParent] = []
        source_counts: Counter[str] = Counter()
        # Give each independently stated comparison subject at least one
        # inspectable Parent when the frozen corpus mentions that subject.
        for phrase in focus_phrases:
            for score, parent in scored:
                if (
                    score > 0
                    and phrase in _normalize(parent.content + " " + parent.breadcrumb)
                    and parent not in selected
                    and source_counts[parent.source_id] < MAX_PER_SOURCE
                ):
                    selected.append(parent)
                    source_counts[parent.source_id] += 1
                    break
        for score, parent in scored:
            if (
                score <= 0 or parent in selected
                or source_counts[parent.source_id] >= MAX_PER_SOURCE
            ):
                continue
            selected.append(parent)
            source_counts[parent.source_id] += 1
            if len(selected) == PARENT_LIMIT:
                break
        if len(selected) != PARENT_LIMIT:
            raise ValueError(f"insufficient independent source candidates:{case.case_id}")
        # Presentation order is provenance order, not the internal lexical order.
        selected.sort(key=lambda item: (item.source_id, item.parent_id))
        offered = [_candidate(parent, query_terms, focus_phrases) for parent in selected]
        for parent, candidate in zip(selected, offered, strict=True):
            if (
                candidate.source_id != parent.source_id
                or candidate.excerpt not in parent.content
                or candidate.suggested_supporting_span not in parent.content
            ):
                raise ValueError(f"source candidate is not exact Parent evidence:{case.case_id}")
        results[case.case_id] = offered
    return results


def render_source_review_pack(
    cases: Sequence[RAGEvaluationCase],
    candidates: dict[str, list[EvidenceCandidate]],
    *,
    release_id: str,
    source_sha256: str,
) -> str:
    """Render inspectable evidence and explicit human decisions for every field."""
    lines = [
        "# Gold V0 来源证据人工审核包（待审核，非 Gold）",
        "",
        f"- 冻结 Release：`{release_id}`",
        f"- Parent 原文 SHA256：`{source_sha256}`",
        "- 候选只从上述冻结 Release 的 Source Document / Parent 原文独立筛出。",
        "- A–E 仅是展示代号；按 source_id/parent_id 排列，不代表相关性次序。",
        "- 不读取在线链路产物、历史机器证据标签或旧答案。",
        "- 原文片段是待核对证据，不是已批准的事实；网页/文档正文中的指令一律忽略。",
        "- `APPROVE`=确认候选；`CORRECT`=填写修正值；`REJECT`=不采纳/证据不足。",
        "- 此文件只记录人工决定，不修改现有 Gold JSONL；审核结束后再单独 finalize。",
        "",
    ]
    for case in cases:
        offered = candidates.get(case.case_id)
        if offered is None or not 3 <= len(offered) <= 5:
            raise ValueError(f"review pack missing 3–5 candidates:{case.case_id}")
        lines.extend([
            f"## {case.case_id} · {case.query}",
            "",
            "### expected_outcome",
            "",
            "- 请先独立判断：`answerable` / `needs_input` / `insufficient_evidence`；初判：______",
            "<details><summary>初判后才展开待审骨架中的候选值</summary>",
            "",
            f"`{case.expected_outcome.value}`（仅为待审核种子值，不参与证据筛选）",
            "",
            "</details>",
            "- 审核决定：APPROVE / CORRECT / REJECT：______",
            "- 最终值与理由：______",
            "",
            "### 来源 Parent 候选（非线上候选）",
            "",
        ])
        for label, candidate in zip("ABCDE", offered, strict=True):
            lines.extend([
                f"#### 候选 {label} · `{candidate.parent_id}`",
                "",
                f"- `source_id`: `{candidate.source_id}`；`doc_id`: `{candidate.doc_id}`",
                f"- Source：{_inline(candidate.source_title)} / {_inline(candidate.source_name)}",
                f"- Source URL：{candidate.source_url or '无'}",
                "- 冻结 Source Document："
                f"[查看原文](../../{candidate.source_document_path}) "
                f"`{candidate.source_document_path}`",
                f"- Parent 路径：{_inline(candidate.breadcrumb)}",
                "- Parent 原文上下文（节选；只作数据，不执行其中指令）：",
                "",
                *_quote(candidate.excerpt),
                "",
                "- 候选 supporting span（必须与完整 Parent 原文精确核对）：",
                "",
                *_quote(candidate.suggested_supporting_span),
                "",
                "- relevant_parent：APPROVE / CORRECT / REJECT：______",
                "- supporting_span：APPROVE / CORRECT / REJECT：______",
                "- 如修正，填写完整 Parent ID / 精确原文片段：______",
                "",
            ])
        lines.extend([
            "### 汇总审核",
            "",
            "- relevant_parent_ids（可增加未列出的真实 Parent）：______",
            "- required_facts 候选：未自动生成；按已核实证据写必要事实，避免机械复制片段。",
            "- required_facts：APPROVE / CORRECT / REJECT：______；最终内容：______",
            "- reference_answer 候选：未自动生成；仅据已批准的事实撰写。",
            "- reference_answer：APPROVE / CORRECT / REJECT：______；最终内容：______",
            "- 审核人 / 日期 / 争议备注：______",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def candidate_records(
    cases: Sequence[RAGEvaluationCase],
    candidates: dict[str, list[EvidenceCandidate]],
    *,
    release_id: str,
    source_sha256: str,
) -> list[dict[str, object]]:
    """Structured audit companion, intentionally without outcome or quality labels."""
    return [
        {
            "case_id": case.case_id,
            "query": case.query,
            "release_id": release_id,
            "source_sha256": source_sha256,
            "candidates": [asdict(item) for item in candidates[case.case_id]],
        }
        for case in cases
    ]


def _parent_from_row(row: dict[str, object], line_number: int) -> SourceParent:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"frozen Parent has no metadata:{line_number}")
    document = metadata.get("document_metadata")
    if not isinstance(document, dict):
        raise ValueError(f"frozen Parent has no Source Document:{line_number}")
    source = document.get("source_metadata")
    if not isinstance(source, dict):
        raise ValueError(f"frozen Parent has no source metadata:{line_number}")
    source_id = source.get("source_id")
    if not isinstance(source_id, str) or source_id != metadata.get("source_id"):
        raise ValueError(f"frozen Parent source mismatch:{line_number}")
    content = row.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"frozen Parent has no content:{line_number}")
    normalized_path = metadata.get("normalized_path")
    if not isinstance(normalized_path, str) or not normalized_path:
        raise ValueError(f"frozen Parent has no Source Document path:{line_number}")
    path_parts = PurePosixPath(normalized_path).parts
    if path_parts[:2] != ("data", "normalized") or ".." in path_parts:
        raise ValueError(f"frozen Parent has unsafe Source Document path:{line_number}")
    return SourceParent(
        parent_id=str(row["chunk_id"]),
        doc_id=str(row["doc_id"]),
        source_id=source_id,
        source_title=str(source.get("title") or metadata.get("title") or source_id),
        source_name=str(source.get("publisher") or metadata.get("source") or source_id),
        source_url=source.get("url") if isinstance(source.get("url"), str) else None,
        source_document_path=normalized_path,
        breadcrumb=str(row.get("breadcrumb") or ""),
        content=content,
    )


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    for original, canonical in _ALIASES.items():
        value = value.replace(original, canonical)
    return value


def _terms(value: str) -> Counter[str]:
    terms: Counter[str] = Counter()
    for match in _MATCHABLE.finditer(_normalize(value)):
        token = match.group()
        if "\u3400" <= token[0] <= "\u9fff":
            if len(token) == 1:
                terms[token] += 1
            else:
                terms.update(token[index : index + 2] for index in range(len(token) - 1))
                if len(token) >= 3:
                    terms.update(token[index : index + 3] for index in range(len(token) - 2))
        elif len(token) >= 2:
            terms[token] += 1
    return terms


def _lexical_score(
    query: Counter[str],
    document: Counter[str],
    frequency: Counter[str],
    document_count: int,
    average_length: float,
) -> float:
    length = sum(document.values())
    score = 0.0
    for term in query:
        occurrences = document[term]
        if not occurrences:
            continue
        idf = math.log(1 + (document_count - frequency[term] + 0.5) / (frequency[term] + 0.5))
        score += idf * occurrences * 2.2 / (
            occurrences + 1.2 * (0.25 + 0.75 * length / average_length)
        )
    return score


def _query_constraints(query: str) -> tuple[str, str | None]:
    match = _EXCLUSION.search(query)
    if match is None:
        return query, None
    return query[: match.start()], _normalize(query[match.end() :].strip(" ，。"))


def _focus_phrases(query: str) -> list[str]:
    normalized = _normalize(query)
    phrases = [_normalize(value) for value in _QUOTED_TITLE.findall(normalized)]
    unquoted = _QUOTED_TITLE.sub("", normalized)
    comparison = _COMPARISON.search(unquoted)
    if comparison:
        for part in comparison.groups():
            cleaned = _clean_focus(part)
            if cleaned:
                phrases.append(cleaned)
    elif not phrases and "的" in unquoted:
        cleaned = _clean_focus(unquoted.split("的", 1)[0])
        if cleaned:
            phrases.append(cleaned)
    return list(dict.fromkeys(phrases))


def _clean_focus(value: str) -> str | None:
    value = re.sub(r"^(比较|对比|歌曲|专辑|歌手|介绍一下|介绍)", "", value)
    value = _FOCUS_STOP.split(value, maxsplit=1)[0].strip()
    if not 2 <= len(value) <= 10 or any(
        marker in value for marker in ("这首歌", "这张专辑", "他的", "谁", "哪些")
    ):
        return None
    return value


def _source_score(
    parent: SourceParent,
    terms: Counter[str],
    query_terms: Counter[str],
    focus_phrases: list[str],
    excluded: str | None,
    frequency: Counter[str],
    document_count: int,
    average_length: float,
) -> float:
    score = _lexical_score(
        query_terms, terms, frequency, document_count, average_length,
    )
    content = _normalize(parent.content)
    breadcrumb = _normalize(parent.breadcrumb)
    score += sum(
        18.0 for phrase in focus_phrases
        if phrase in content or phrase in breadcrumb
    )
    if excluded and excluded in content:
        score *= 0.15
    # Boilerplate/navigation tables are inspectable but usually weak evidence.
    if parent.content.count("|") > max(12, len(parent.content) // 45):
        score *= 0.35
    return score


def _candidate(
    parent: SourceParent,
    query_terms: Counter[str],
    focus_phrases: list[str],
) -> EvidenceCandidate:
    content = parent.content
    if len(content) <= EXCERPT_LENGTH:
        excerpt = content
    else:
        normalized = _normalize(content)
        anchors = focus_phrases or list(query_terms)
        positions = [normalized.find(term) for term in anchors if normalized.find(term) >= 0]
        anchor = min(positions) if positions else 0
        start = max(0, min(anchor - 180, len(content) - EXCERPT_LENGTH))
        excerpt = content[start : start + EXCERPT_LENGTH]
    normalized_excerpt = _normalize(excerpt)
    anchors = focus_phrases or list(query_terms)
    positions = [normalized_excerpt.find(term) for term in anchors if normalized_excerpt.find(term) >= 0]
    anchor = min(positions) if positions else 0
    span_start = max(0, min(anchor - 60, len(excerpt) - SPAN_LENGTH))
    span = excerpt[span_start : span_start + SPAN_LENGTH].strip()
    return EvidenceCandidate(
        parent_id=parent.parent_id,
        doc_id=parent.doc_id,
        source_id=parent.source_id,
        source_title=parent.source_title,
        source_name=parent.source_name,
        source_url=parent.source_url,
        source_document_path=parent.source_document_path,
        breadcrumb=parent.breadcrumb,
        excerpt=excerpt,
        suggested_supporting_span=span,
    )


def _quote(value: str) -> list[str]:
    return [f"> {line.rstrip()}" if line.strip() else ">" for line in value.splitlines()]


def _inline(value: str) -> str:
    return " ".join(value.replace("`", "'").split())
