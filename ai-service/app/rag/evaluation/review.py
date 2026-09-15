"""Human-readable review pack for approving RAG evaluation labels."""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.evaluation.models import (
    EvaluationContext,
    RAGEvaluationCase,
    RAGEvaluationTrace,
)


def build_annotation_review_markdown(
    cases: Sequence[RAGEvaluationCase],
    traces: Sequence[RAGEvaluationTrace],
) -> str:
    trace_by_case = {item.case_id: item for item in traces}
    if len(trace_by_case) != len(traces):
        raise ValueError("annotation review trace case ids must be unique")
    missing = [item.case_id for item in cases if item.case_id not in trace_by_case]
    if missing:
        raise ValueError(f"annotation review traces missing cases:{','.join(missing)}")
    run_ids = sorted({item.run_id for item in traces})
    lines = [
        "# MoodFlow RAG Eval V0 人工审核包",
        "",
        f"- Trace Run：`{', '.join(run_ids)}`",
        f"- 样本数：{len(cases)}",
        "- 说明：勾选只是审核记录；正式标签仍写回 approved JSONL。",
        "",
    ]
    for case in cases:
        trace = trace_by_case[case.case_id]
        lines.extend(
            [
                f"## {case.case_id} · {case.query}",
                "",
                f"- 预期结果：`{case.expected_outcome.value}`",
                f"- Query Understanding：`{trace.statuses.query_understanding}`",
                f"- Retrieval / Rerank / Context：`{trace.statuses.retrieval}` / "
                f"`{trace.statuses.rerank}` / `{trace.statuses.context}`",
                "- [ ] 查询范围与预期结果正确",
                "- [ ] 已确认 relevant Parent 与 supporting span",
                "- [ ] 已填写 required facts 与 reference answer",
                "",
            ]
        )
        if trace.statuses.query_understanding == "needs_input":
            lines.extend(["该样本预期触发追问，无需填写回答证据。", ""])
            continue
        final_ids = {item.parent_id for item in trace.final_context}
        reranked_ids = {item.parent_id for item in trace.reranked_context}
        lines.extend(["### 最终上下文", ""])
        lines.extend(_render_contexts(trace.final_context, excerpt_limit=1_200))
        lines.extend(["<details>", "<summary>其余 Rerank / Candidate 候选</summary>", ""])
        rerank_remainder = [
            item for item in trace.reranked_context if item.parent_id not in final_ids
        ]
        candidate_remainder = [
            item for item in trace.candidate_pool if item.parent_id not in reranked_ids
        ]
        lines.extend(["#### Rerank Top12 未进入最终上下文", ""])
        lines.extend(_render_contexts(rerank_remainder, excerpt_limit=500))
        lines.extend(["#### Top40 其余候选", ""])
        lines.extend(_render_contexts(candidate_remainder, excerpt_limit=300))
        lines.extend(["</details>", ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_contexts(
    contexts: Sequence[EvaluationContext],
    *,
    excerpt_limit: int,
) -> list[str]:
    if not contexts:
        return ["_无候选_", ""]
    lines: list[str] = []
    for item in contexts:
        excerpt = " ".join(item.content.split())
        if len(excerpt) > excerpt_limit:
            excerpt = excerpt[: excerpt_limit - 1].rstrip() + "…"
        lines.extend(
            [
                f"#### #{item.rank} `{item.parent_id}` · {item.title}",
                "",
                f"来源：`{item.source_id}` / {item.source_name}",
                "",
                f"> {excerpt}",
                "",
                "- [ ] relevant",
                "- [ ] supporting span 已核对",
                "",
            ]
        )
    return lines
