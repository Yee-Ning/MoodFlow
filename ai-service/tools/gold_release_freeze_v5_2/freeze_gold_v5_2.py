"""Apply the ten West-approved Phase 2 updates without rewriting other lines."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat
import sys

AI_ROOT = Path(__file__).resolve().parents[2]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from app.rag.indexing.release import KnowledgeReleaseValidator  # noqa: E402
from tools.gold_release_freeze_v5.freeze_gold_v5 import (  # noqa: E402
    freeze_anchors,
    readonly,
)

REPO = AI_ROOT.parent
PLAN = Path(__file__).with_name("approved_plan.json")
PLAN_SHA = "c5e545f65d682aab359ccea0d383d84daa1916779b917e6232159c7b77dbbfd7"
V5_SHA = "b708332729177a095cb0993381bc9b3ffaef7808846e61bbdb8cbd0877afbfad"
V51_SHA = "57ece040c085c5c2a21f3e380c0550781f756fafb73c14d1702d05352a3a0649"
RELEASE_ID = "knowledge-v2-ce001e1a586e2c20"
RELEASE = (
    AI_ROOT / "data/reruns/jay-chou-release-freeze-20261019/run01/data/knowledge/v2"
)
BASE = AI_ROOT / "evals/datasets/rag_eval_gold_v5_1.frozen.jsonl"
V5 = AI_ROOT / "evals/datasets/rag_eval_gold_v5.frozen.jsonl"
TARGET = AI_ROOT / "evals/datasets/rag_eval_gold_v5_2.frozen.jsonl"
REPORT = REPO / "workspace/moodflow-review/gold-v5-2-phase2-20261019"
LABELS = {
    f"rag-v0-{n:03}": label
    for n, label in (
        (2, "full"),
        (6, "partial"),
        (7, "full"),
        (23, "full"),
        (61, "full"),
        (62, "full"),
        (63, "full"),
        (64, "full"),
        (65, "partial"),
        (66, "partial"),
    )
}
DISTRIBUTION = {"full": 24, "partial": 10, "insufficient": 9, "needs_input": 7}
ALLOWED_FIELDS = {
    "release_id",
    "frozen_by",
    "frozen_at",
    "relevant_parent_ids",
    "answerability",
    "ground_truth",
    "required_facts",
    "anchors",
}
GAPS = {
    "rag-v0-065": "逐首快歌对应的具体电影清单缺乏证据",
    "rag-v0-066": "《娘子》分歧的具体词句缺乏证据",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def records(raw: bytes) -> tuple[list[bytes], list[dict]]:
    lines = raw.splitlines(keepends=True)
    if len(lines) != 50 or any(not line.strip() for line in lines):
        raise ValueError("expected_50_physical_lines")
    rows = [json.loads(line) for line in lines]
    if len({r["case_id"] for r in rows}) != 50:
        raise ValueError("duplicate_case_id")
    return lines, rows


def load_plan(path: Path = PLAN) -> dict:
    if file_sha(path) != PLAN_SHA:
        raise ValueError("approved_plan_hash_mismatch")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (
        plan["approved_by"] != "West"
        or plan["target_release_id"] != RELEASE_ID
        or {key: c["answerability"] for key, c in plan["cases"].items()} != LABELS
    ):
        raise ValueError("approved_scope_mismatch")
    return plan


def load_corpus(path: Path) -> dict[str, dict]:
    """Retain exact text and identity; do not load knowledge or large metadata."""
    result = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            value = json.loads(line)
            key = value["chunk_id"]
            if key in result:
                raise ValueError("duplicate_corpus_chunk")
            result[key] = {
                "chunk_id": key,
                "parent_id": value.get("parent_id"),
                "doc_id": value["doc_id"],
                "content": value["content"],
                "metadata": {"source_id": value["enrichment"]["source"]["source_id"]},
            }
    for row in result.values():
        if row["parent_id"] is not None:
            parent = result[row["parent_id"]]
            if parent["parent_id"] is not None or parent["doc_id"] != row["doc_id"]:
                raise ValueError("corpus_parent_mismatch")
    return result


def build_anchors(case: dict, corpus: dict[str, dict]) -> list[dict]:
    parents = {key for key, row in corpus.items() if row["parent_id"] is None}
    children = set(corpus) - parents
    by_parent = defaultdict(list)
    for key in children:
        by_parent[corpus[key]["parent_id"]].append(corpus[key])
    prepared = []
    ids = set()
    for item in case["anchors"]:
        if item["anchor_id"] in ids:
            raise ValueError("duplicate_anchor_id")
        ids.add(item["anchor_id"])
        parent_id, quote = item["parent_id"], item["exact_text"]
        if parent_id not in parents or parent_id not in case["relevant_parent_ids"]:
            raise ValueError("unapproved_parent")
        parent = corpus[parent_id]
        matches, matched_children = [], []
        for row in [parent, *sorted(by_parent[parent_id], key=lambda r: r["chunk_id"])]:
            start = row["content"].find(quote)
            if not quote or start < 0:
                if row is parent:
                    raise ValueError("approved_quote_not_in_parent")
                continue
            matches.append(
                {
                    "chunk_id": row["chunk_id"],
                    "field": "content",
                    "quote": quote,
                    "raw_start": start,
                    "raw_end": start + len(quote),
                }
            )
            if row["parent_id"] is not None:
                matched_children.append(row["chunk_id"])
        prepared.append(
            {
                **item,
                "status": "matched",
                "accepted_locations": [
                    {
                        "source_id": parent["metadata"]["source_id"],
                        "parent_id": parent_id,
                        "child_ids": matched_children,
                        "matches": matches,
                    }
                ],
            }
        )
    return freeze_anchors(
        {"suggested_label": case["answerability"], "anchors": prepared},
        corpus,
        parents,
        children,
    )


def validate_landmines(row: dict) -> None:
    key, text = row["case_id"], row["ground_truth"]
    facts = row["required_facts"]
    if key == "rag-v0-006":
        if "日本乡间" not in text or any(
            s in text for s in ("海参崴", "海參崴", "海参威", "海參威")
        ):
            raise ValueError("006_mv_location_landmine")
    if key in {"rag-v0-023", "rag-v0-066"}:
        if not all(s in text for s in ("2005年访谈", "当时", "两首")):
            raise ValueError("023_two_exceptions_temporal_scope")
    if key == "rag-v0-064":
        methods = (
            "文献回顾",
            "转谱",
            "乐谱分析",
            "歌词分析",
            "聆听分析",
            "视频分析",
            "虚拟田野调查",
        )
        if not all(
            s in text for s in ("《菊花台》", "《千里之外》", "定性", *methods)
        ) or any(s in text for s in ("《青花瓷》", "《霍元甲》")):
            raise ValueError("064_selected_songs_or_methods_landmine")
    if key in GAPS:
        if GAPS[key] not in facts or ("无法" not in text and "没有提供" not in text):
            raise ValueError("065_066_required_gap_removed")


def validate_payload(
    base: bytes, result: bytes, plan: dict, corpus: dict, timestamp: str
):
    before_lines, before = records(base)
    after_lines, after = records(result)
    if [r["case_id"] for r in before] != [r["case_id"] for r in after]:
        raise ValueError("case_order_changed")
    changed = []
    for line_before, line_after, old, new in zip(
        before_lines, after_lines, before, after, strict=True
    ):
        key = old["case_id"]
        if key not in LABELS:
            if line_before != line_after:
                raise ValueError("unapproved_raw_line_changed:" + key)
            continue
        if line_before == line_after:
            raise ValueError("approved_case_not_updated:" + key)
        changed.append(key)
        if set(old) != set(new) or any(
            old[k] != new[k] for k in old if k not in ALLOWED_FIELDS
        ):
            raise ValueError("unapproved_field_changed:" + key)
        case = plan["cases"][key]
        expected = {
            "release_id": RELEASE_ID,
            "frozen_by": "West",
            "frozen_at": timestamp,
            "relevant_parent_ids": case["relevant_parent_ids"],
            "answerability": LABELS[key],
            "ground_truth": case["ground_truth"],
            "required_facts": [f["text"] for f in case["facts"]],
            "anchors": build_anchors(case, corpus),
        }
        if any(new[k] != value for k, value in expected.items()):
            raise ValueError("row_differs_from_plan_or_exact_evidence:" + key)
        if set(new["relevant_parent_ids"]) != {a["parent_id"] for a in new["anchors"]}:
            raise ValueError("parent_anchor_set_mismatch:" + key)
        anchor_ids = {a["anchor_id"] for a in new["anchors"]}
        for fact in case["facts"]:
            if fact["kind"] == "evidence" and (
                not fact["anchor_ids"] or not set(fact["anchor_ids"]) <= anchor_ids
            ):
                raise ValueError("positive_fact_has_no_approved_evidence")
            if fact["kind"] == "scope_limit" and fact["anchor_ids"]:
                raise ValueError("scope_limit_must_not_fake_positive_proof")
        validate_landmines(new)
    if (
        set(changed) != set(LABELS)
        or Counter(r["answerability"] for r in after) != DISTRIBUTION
    ):
        raise ValueError("scope_or_distribution_mismatch")
    return after


def build_payload(base: bytes, plan: dict, corpus: dict, timestamp: str) -> bytes:
    parsed_time = datetime.fromisoformat(timestamp)
    if parsed_time.utcoffset() != timedelta(hours=8):
        raise ValueError("frozen_at_requires_Asia_Shanghai_offset")
    lines, rows = records(base)
    output = []
    for original_line, old in zip(lines, rows, strict=True):
        key = old["case_id"]
        if key not in LABELS:
            output.append(original_line)
            continue
        case, new = plan["cases"][key], deepcopy(old)
        new.update(
            {
                "release_id": RELEASE_ID,
                "frozen_by": "West",
                "frozen_at": timestamp,
                "relevant_parent_ids": case["relevant_parent_ids"],
                "answerability": case["answerability"],
                "ground_truth": case["ground_truth"],
                "required_facts": [f["text"] for f in case["facts"]],
                "anchors": build_anchors(case, corpus),
            }
        )
        ending = (
            b"\r\n"
            if original_line.endswith(b"\r\n")
            else b"\n"
            if original_line.endswith(b"\n")
            else b""
        )
        output.append(json.dumps(new, ensure_ascii=False).encode("utf-8") + ending)
    result = b"".join(output)
    validate_payload(base, result, plan, corpus, timestamp)
    return result


def publish_new(path: Path, content: bytes) -> None:
    """Never truncate or unlock a pre-existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    path.chmod(stat.S_IREAD)
    if path.read_bytes() != content or not readonly(path):
        raise ValueError("freeze_post_write_or_readonly_check_failed")


def verify_inputs() -> tuple[dict, bytes, dict, dict[Path, str]]:
    plan = load_plan()
    if file_sha(V5) != V5_SHA or file_sha(BASE) != V51_SHA:
        raise ValueError("protected_gold_hash_mismatch")
    if not readonly(V5) or not readonly(BASE):
        raise ValueError("protected_gold_not_readonly")
    release = KnowledgeReleaseValidator(
        AI_ROOT, knowledge_path=RELEASE / "knowledge_enriched_chunks.jsonl"
    ).validate()
    if (
        release.release_id != RELEASE_ID
        or release.manifest_fingerprint
        != "sha256:" + plan["target_release_manifest_sha256"]
    ):
        raise ValueError("approved_release_mismatch")
    corpus = load_corpus(RELEASE / "knowledge_enriched_chunks.jsonl")
    release_manifest = json.loads(
        (RELEASE / "knowledge_release_manifest.json").read_text(encoding="utf-8")
    )
    protected = {V5: V5_SHA, BASE: V51_SHA, PLAN: PLAN_SHA}
    protected[RELEASE / "knowledge_release_manifest.json"] = plan[
        "target_release_manifest_sha256"
    ]
    protected.update(
        {
            RELEASE / name: value.removeprefix("sha256:")
            for name, value in release_manifest["artifacts"].items()
        }
    )
    return plan, BASE.read_bytes(), corpus, protected


def build_reports(
    base: bytes, result: bytes, plan: dict, timestamp: str, protected: dict
):
    old_lines, old_rows = records(base)
    new_lines, rows = records(result)
    changes, preserved = [], {}
    for index, (old_line, new_line, old, new) in enumerate(
        zip(old_lines, new_lines, old_rows, rows, strict=True), 1
    ):
        key = old["case_id"]
        if key not in LABELS:
            preserved[key] = sha(old_line)
            continue
        fields = [k for k in old if old[k] != new[k]]
        changes.append(
            {
                "case_id": key,
                "line": index,
                "question": old["question"],
                "changed_fields": fields,
                "before_line_sha256": sha(old_line),
                "after_line_sha256": sha(new_line),
                "before": {k: old[k] for k in sorted(ALLOWED_FIELDS)},
                "after": {k: new[k] for k in sorted(ALLOWED_FIELDS)},
                "fact_alignment": plan["cases"][key]["facts"],
            }
        )
    manifest = {
        "manifest_version": "gold-v5_2-freeze-v1",
        "status": "frozen",
        "target": TARGET.relative_to(REPO).as_posix(),
        "sha256": sha(result),
        "schema_version": "v5",
        "approved_by": "West",
        "frozen_at": timestamp,
        "authorization_source": plan["authorization_source"],
        "metadata_scope": plan["metadata_scope"],
        "changed_row_release_id": RELEASE_ID,
        "row_count": 50,
        "unique_case_ids": 50,
        "changed_case_ids": list(LABELS),
        "unchanged_raw_line_count": 40,
        "unchanged_raw_line_sha256": preserved,
        "distribution": DISTRIBUTION,
        "readonly_verified": True,
        "checks": {
            "exact_10_case_scope": True,
            "other_40_lines_identical": True,
            "questions_and_schema_unchanged": True,
            "all_parent_child_quotes_valid": True,
            "all_positive_facts_have_anchor_bindings": True,
            "006_023_064_landmines": True,
            "065_066_gaps_retained": True,
            "protected_input_sha256_unchanged": True,
        },
        "protected_inputs": {
            p.relative_to(REPO).as_posix(): digest for p, digest in protected.items()
        },
        "code_sha256": file_sha(Path(__file__)),
    }
    lines = [
        "# Gold V5.2 Phase 2 冻结报告",
        "",
        "West 已批准的 10 题已执行；未启动检索或模型评测。",
        "",
        f"SHA256：`{sha(result)}`。冻结执行时间：`{timestamp}`。",
        "",
        "最终分布：**full24 / partial10 / insufficient9 / needs_input7**；50 行、50 个唯一 case_id。",
        "",
        "只更新 002、006、007、023、061–066；其他 40 行逐字节保留，包括原 release_id、frozen_by、frozen_at。",
        "10 个更新行绑定 knowledge-v2-ce001e1a586e2c20，frozen_by=West，schema_version=v5；整文件已设只读。",
        "contexts、answer、question、old_label、changes 均未改；changes 保留旧迁移历史，本次事实及前后对照见 case-changes.json。",
        "",
        "006：歌曲 MV 只写日本乡间。023/066：两首先词例外限定在 2005 年受访时。064：只选菊花台、千里之外及摘要七种方法。",
        "065/066：保留原 required_facts 缺口原文，并在 ground_truth 明确无法回答部分；没有为缺口伪造 matched anchor。",
        "",
    ]
    for change in changes:
        key, new = change["case_id"], change["after"]
        lines += [
            f"**{key}：{change['question']}**",
            "",
            f"标签：{change['before']['answerability']} → {new['answerability']}。",
            "",
            "Parent：" + "、".join(f"`{p}`" for p in new["relevant_parent_ids"]) + "。",
            "",
            "ground_truth：" + new["ground_truth"],
            "",
            "required_facts：",
            "",
        ]
        lines += ["- " + f for f in new["required_facts"]]
        lines += [
            "",
            "证据原文（Parent/Child 及精确字符偏移见 case-changes.json）：",
            "",
        ]
        for anchor in new["anchors"]:
            lines += [
                f"- `{anchor['anchor_id']}` / `{anchor['parent_id']}` / `{anchor['source_id']}`："
                + anchor["exact_text"].replace("\n", " ")
            ]
        lines.append("")
    lines += [
        "V5 / V5.1 frozen-2 哈希与只读属性保持不变；本次未修改 registry、知识 release、Qdrant 或生产配置。",
        "已验证本任务数据与证据约束；未做生成质量评测、检索评测或前端/API构建，本次没有对应实现改动。",
        "只做本地分支提交，不 push、不合入 main。",
        "",
    ]
    return manifest, changes, "\n".join(lines).encode("utf-8")


def run(check_only: bool = False):
    plan, base, corpus, protected = verify_inputs()
    if check_only:
        _, existing = records(TARGET.read_bytes())
        timestamp = next(r["frozen_at"] for r in existing if r["case_id"] in LABELS)
    else:
        if TARGET.exists() or REPORT.exists():
            raise ValueError("immutable_output_already_exists_use_check")
        timestamp = datetime.now(timezone(timedelta(hours=8))).isoformat(
            timespec="seconds"
        )
    result = build_payload(base, plan, corpus, timestamp)
    manifest, changes, report = build_reports(base, result, plan, timestamp, protected)
    outputs = {
        REPORT / "freeze-manifest.json": json_bytes(manifest),
        REPORT / "case-changes.json": json_bytes(changes),
        REPORT / "report.md": report,
        REPORT / "rag_eval_gold_v5_2.frozen.jsonl.sha256": (
            sha(result) + "  rag_eval_gold_v5_2.frozen.jsonl\n"
        ).encode(),
    }
    if any(file_sha(p) != digest for p, digest in protected.items()):
        raise ValueError("protected_input_changed_before_publish")
    if check_only:
        if TARGET.read_bytes() != result or not readonly(TARGET):
            raise ValueError("frozen_output_changed_or_not_readonly")
        if any(not p.exists() or p.read_bytes() != data for p, data in outputs.items()):
            raise ValueError("freeze_report_missing_or_changed")
    else:
        publish_new(TARGET, result)
        REPORT.mkdir(parents=True)
        for path, data in outputs.items():
            with path.open("xb") as handle:
                handle.write(data)
    if any(file_sha(p) != digest for p, digest in protected.items()):
        raise ValueError("protected_input_changed_after_publish")
    if not readonly(V5) or not readonly(BASE) or not readonly(TARGET):
        raise ValueError("readonly_attribute_changed")
    return {
        "mode": "check" if check_only else "freeze",
        "sha256": sha(result),
        "distribution": DISTRIBUTION,
        "changed_rows": 10,
        "unchanged_raw_lines": 40,
        "readonly": True,
        "frozen_at": timestamp,
        "target": str(TARGET),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify bytes and attributes without writing",
    )
    args = parser.parse_args()
    print(json.dumps(run(args.check), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
