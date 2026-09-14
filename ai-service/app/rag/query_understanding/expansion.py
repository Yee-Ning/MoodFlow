"""Bounded, deterministic query expansion policy."""

from app.rag.query_understanding.models import (
    QueryAnalysis,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    SearchChannel,
)


_SEMANTIC_CUES = {
    QueryType.TIMELINE: "时间线 发展 阶段",
    QueryType.RELATIONSHIP: "合作 关系 创作",
    QueryType.INFLUENCE_STYLE: "音乐风格 影响 演变",
    QueryType.EXPLORATORY: "背景 经历 影响",
}


def _append_unique(
    result: list[QueryVariant], candidate: QueryVariant, *, maximum: int
) -> None:
    if len(result) >= maximum:
        return
    normalized = " ".join(candidate.text.split()).casefold()
    if any(" ".join(item.text.split()).casefold() == normalized for item in result):
        return
    result.append(candidate)


def expand_queries(
    analysis: QueryAnalysis,
    *,
    maximum: int,
    enable_hyde: bool = False,
) -> list[QueryVariant]:
    if enable_hyde:
        raise ValueError("HyDE is intentionally unsupported in query-understanding v1")
    result = [
        QueryVariant(
            text=analysis.standalone_query,
            kind=QueryVariantKind.ORIGINAL,
            channel=SearchChannel.BOTH,
            weight=1.0,
        )
    ]
    alias_terms: list[str] = []
    for entity in analysis.entities:
        if entity.canonical_name:
            alias_terms.append(entity.canonical_name)
        alias_terms.extend(entity.aliases[:3])
    alias_terms = list(dict.fromkeys(item.strip() for item in alias_terms if item.strip()))
    if alias_terms:
        _append_unique(
            result,
            QueryVariant(
                text=" ".join(alias_terms[:8]),
                kind=QueryVariantKind.ALIAS,
                channel=SearchChannel.LEXICAL,
                weight=0.85,
            ),
            maximum=maximum,
        )

    if QueryType.COMPARISON in analysis.query_types and len(analysis.entities) > 1:
        for entity in analysis.entities:
            if entity.canonical_name is None:
                continue
            _append_unique(
                result,
                QueryVariant(
                    text=f"{entity.canonical_name} {analysis.standalone_query}",
                    kind=QueryVariantKind.DECOMPOSED,
                    channel=SearchChannel.BOTH,
                    weight=0.8,
                ),
                maximum=maximum,
            )
    else:
        cue = next(
            (
                value
                for query_type, value in _SEMANTIC_CUES.items()
                if query_type in analysis.query_types
            ),
            None,
        )
        if cue:
            _append_unique(
                result,
                QueryVariant(
                    text=f"{analysis.standalone_query} {cue}",
                    kind=QueryVariantKind.SEMANTIC,
                    channel=SearchChannel.DENSE,
                    weight=0.75,
                ),
                maximum=maximum,
            )
    return result
