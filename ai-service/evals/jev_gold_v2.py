"""Freeze capability-adjudicated Router Gold V2 without editing historical labels."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evals/datasets/router_eval_v2.jsonl"
CONTRACT = ROOT / "evals/config/router_capability_contract.json"
V1_DIAGNOSTIC = ROOT / "evals/datasets/jev_router_baseline_v1.jsonl"
OUTPUT = ROOT / "evals/datasets/router_eval_gold_v2.jsonl"
REVIEW = ROOT / "evals/reports/router_gold_v2_review.md"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


ROUTES = tuple(json.loads(CONTRACT.read_text(encoding="utf-8"))["route_keys"])
QUESTIONABLE = {row["case_id"] for row in read_jsonl(V1_DIAGNOSTIC)
                if row["label_status"] == "questionable"}

# Each V1 questionable case is decided exactly once. GeneralChat labels are
# limited to cautious, non-factual hypotheticals; they do not claim a simulated
# historical outcome. Unresolvable factual requests remain negative cases.
DECISIONS: dict[str, tuple[str, list[str], str, str]] = {
    "manual-hard-019": ("RELABEL", ["general_chat"], "可作不主张史实的假设讨论；Worldline 无反事实模拟。", "否定收藏动作不是执行意图；普通审慎假设由 GeneralChat 承接。"),
    "manual-hard-021": ("RELABEL", ["general_chat"], "职业影响仅可讨论可能性，不能生成具体历史因果。", "条件句排除了音量操作；Worldline 不支持未发生事件的影响图。"),
    "manual-hard-027": ("RELABEL", ["general_chat"], "对华语乐坛的变化只能作明确不确定的开放讨论。", "没有可检索证据能证明未发行后的现实；GeneralChat 可表达假设与限制。"),
    "manual-multi-011": ("RELABEL", ["current_song", "general_chat"], "当前歌曲分析仍有效；专辑晚发行是假设讨论，不是 Worldline 关系探索。", "CurrentSong 使用当前 SongContext；GeneralChat 仅讨论可能性，不断言具体发行影响。"),
    "manual-multi-020": ("RELABEL", ["general_chat"], "两个子句均可由普通对话完成，无独立证据图任务。", "晚出道是假设；音乐市场变化是开放讨论，不能把反事实当作 Worldline 事实。"),
    "manual-test-worldline-001": ("RELABEL", ["general_chat"], "陈奕迅未成为歌手是假设讨论，不能做可引用的历史结论。", "具体艺人名称不自动构成 Worldline 探索；没有反事实模拟能力。"),
    "manual-world-002": ("RELABEL", ["general_chat"], "未发行的后果可审慎猜想，但不可称作已证事实。", "指向当前歌曲的反事实问题不属于 CurrentSong 歌词分析，也不是 Worldline 证据图。"),
    "manual-world-007": ("RELABEL", ["general_chat"], "未爆火的结果属于开放假设。", "Worldline 可检索真实关系，不能模拟作品未走红后的历史。"),
    "manual-world-022": ("RELABEL", ["general_chat"], "乐队提前出现是假设；只能给条件化的通用讨论。", "即使可解析乐队实体，当前图仍不支持改变年代的模拟。"),
    "manual-hard-020": ("RELABEL", ["general_chat"], "用户明确排除歌词分析并要求想象性讨论。", "非 CurrentSong；Worldline 不应把未发生事件输出为有来源的关系。"),
    "manual-multi-009": ("RELABEL", ["general_chat", "tool_calling"], "反事实讨论与明确收藏动作是两个任务。", "GeneralChat 不承诺史实；ToolCalling 在有当前歌曲 ID 时排队收藏动作。"),
    "manual-multi-010": ("RELABEL", ["general_chat"], "两个子句都属于假设及其意义的普通对话。", "并未要求真实艺人证据图；Worldline 不能模拟从未出道的历史。"),
    "manual-multi-014": ("RELABEL", ["current_song", "general_chat"], "伤感听感是 CurrentSong；晚发行是假设讨论。", "需当前 SongContext 做第一问；第二问不应输出 Worldline 虚构关系。"),
    "manual-multi-025": ("REMOVE_FROM_SCORING", [], "歌曲事实背景核查和反事实影响无法由现有子图完整满足。", "CurrentSong 无 RAG 背景核查；Worldline 缺可解析具名根实体且无反事实模拟。收藏虽可执行，但没有一个可计分的完整 route set。"),
    "manual-world-017": ("REMOVE_FROM_SCORING", [], "制作人与专辑均未给出可解析身份，且请求反事实制作史。", "Worldline 根实体仅 song/artist/album；GeneralChat 无当前专辑/制作人事实，不能给出特定作品结论。"),
}

# Paired contrasts are new capability-derived single-intent Gold. They are
# distinct from the historical-label correction cohort in the ablation.
BOUNDARY_PAIRS = [
    ("concept-harmony", ("boundary-v2-general-harmony", "什么是和声？", "general_chat"),
     ("boundary-v2-world-influence", "Radiohead 对哪些乐队产生过影响？请给出证据来源。", "worldline")),
    ("concept-album", ("boundary-v2-general-concept-album", "什么叫概念专辑？", "general_chat"),
     ("boundary-v2-world-album", "探索《OK Computer》与哪些专辑存在有证据的影响关系。", "worldline")),
    ("version-scene", ("boundary-v2-general-live", "为什么现场版和录音室版听起来不同？", "general_chat"),
     ("boundary-v2-world-scene", "探索 Nirvana 与哪些音乐场景有关，并给出来源。", "worldline")),
    ("genre-graph", ("boundary-v2-general-genres", "摇滚和流行有什么区别？", "general_chat"),
     ("boundary-v2-world-graph", "查看 Radiohead 的影响关系图和证据来源。", "worldline")),
    ("sadness-context", ("boundary-v2-general-sadness", "一般来说，音乐为什么会让人觉得伤感？", "general_chat"),
     ("boundary-v2-current-sadness", "当前播放的这首歌为什么听起来这么伤感？", "current_song")),
    ("search-explore", ("boundary-v2-tool-search", "搜索 Radiohead 的歌曲。", "tool_calling"),
     ("boundary-v2-world-explore", "探索 Radiohead 的音乐世界线，并列出来源与关系图。", "worldline")),
    ("play-analyze", ("boundary-v2-tool-play", "帮我播放 Creep。", "tool_calling"),
     ("boundary-v2-current-creep", "当前播放的 Creep 为什么听起来这么伤感？", "current_song")),
]

NEGATIVE_CASES = [
    ("cap-negative-01", "如果这首歌从未发行，真实音乐史会怎样变化？", "要求真实反事实历史结论；无子图能验证未发生的事实。"),
    ("cap-negative-02", "帮我预订明天的演唱会门票", "无票务预订工具或子图。"),
]


def freeze_gold() -> Path:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    version = str(contract["version"])
    if set(contract["route_keys"]) != set(ROUTES):
        raise ValueError("contract route keys do not match router")
    if set(DECISIONS) != QUESTIONABLE:
        raise ValueError("all and only V1 questionable cases must be adjudicated")
    source_rows = read_jsonl(SOURCE)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    rows = []
    for source in source_rows:
        case_id = source["case_id"]
        original = source["gold_routes"]
        if case_id in DECISIONS:
            action, final, reason, analysis = DECISIONS[case_id]
        else:
            action, final = "KEEP", original
            reason = "历史标签与当前能力 Contract 一致；本阶段未重新优化该标签。"
            analysis = "按当前子图能力保留原标签。"
        if action == "KEEP" and final != original:
            raise ValueError("KEEP changed a historical label")
        if action == "RELABEL" and (not final or final == original):
            raise ValueError("RELABEL must change to a nonempty label")
        if action == "REMOVE_FROM_SCORING" and final:
            raise ValueError("removed case must have no scored route")
        if not set(final) <= set(ROUTES) or len(final) != len(set(final)):
            raise ValueError("invalid adjudicated routes")
        scoring = ("negative_unsupported" if action == "REMOVE_FROM_SCORING" else
                   "followup_excluded" if source["task"] == "followup_analysis" else "scored")
        rows.append({"case_id": case_id, "query": source["query"],
                     "original_label": original, "adjudicated_label": final,
                     "adjudication_reason": reason, "capability_analysis": analysis,
                     "action": action, "scoring_status": scoring,
                     "original_task": source["task"],
                     "task": "single_intent" if scoring == "scored" and len(final) == 1 else
                             "multi_intent" if scoring == "scored" else source["task"],
                     "tags": source["tags"], "recent_context": source["recent_context"],
                     "label_source": source["label_source"], "source_dataset_sha256": source_hash,
                     "capability_contract_version": version, "review_basis": "capability_adjudication_v2"})
    for pair_id, left, right in BOUNDARY_PAIRS:
        for case_id, query, route in (left, right):
            rows.append({"case_id": case_id, "query": query,
                         "original_label": [], "adjudicated_label": [route],
                         "adjudication_reason": f"成对边界样本 {pair_id}；按当前 Contract 的意图与所需能力标注。",
                         "capability_analysis": "与同 pair 的另一侧相比，仅此请求所需的子图能力成立。",
                         "action": "ADD_BOUNDARY", "scoring_status": "scored",
                         "original_task": None, "task": "single_intent",
                         "tags": ["boundary", "paired", pair_id], "pair_id": pair_id,
                         "recent_context": [], "label_source": "router_capability_contract_v1",
                         "source_dataset_sha256": source_hash,
                         "capability_contract_version": version, "review_basis": "capability_adjudication_v2"})
    for case_id, query, reason in NEGATIVE_CASES:
        rows.append({"case_id": case_id, "query": query,
                     "original_label": [], "adjudicated_label": [],
                     "adjudication_reason": reason, "capability_analysis": reason,
                     "action": "ADD_NEGATIVE", "scoring_status": "negative_unsupported",
                     "original_task": None, "task": "negative", "tags": ["negative", "unsupported"],
                     "recent_context": [], "label_source": "router_capability_contract_v1",
                     "source_dataset_sha256": source_hash,
                     "capability_contract_version": version, "review_basis": "capability_adjudication_v2"})
    if len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate case_id")
    write_jsonl(OUTPUT, rows)
    return OUTPUT


def summary(rows: list[dict]) -> dict:
    historical = [row for row in rows if row["original_task"] is not None]
    scored = [row for row in rows if row["scoring_status"] == "scored"]
    return {
        "historical_actions": dict(Counter(row["action"] for row in historical)),
        "route_support": dict(Counter(route for row in scored for route in row["adjudicated_label"])),
        "single_intent": sum(row["task"] == "single_intent" for row in scored),
        "multi_intent": sum(row["task"] == "multi_intent" for row in scored),
        "boundary": sum("boundary" in row["tags"] for row in rows),
        "new_paired_boundary": sum(row["action"] == "ADD_BOUNDARY" for row in rows),
        "negative_unsupported": sum(row["scoring_status"] == "negative_unsupported" for row in rows),
        "followup_excluded": sum(row["scoring_status"] == "followup_excluded" for row in rows),
        "total": len(rows),
    }


def write_review(rows: list[dict]) -> Path:
    totals = summary(rows)
    decisions = [row for row in rows if row["case_id"] in QUESTIONABLE]
    lines = [
        "# Router Gold V2 capability adjudication",
        "",
        "本轮先依据 `evals/config/router_capability_contract.json` v1.0、四个 Foundation Subgraph 当前代码、"
        "`jev_capability_alignment_v1.md` 和 `foundation_subgraph_e2e_acceptance.md` 裁决，"
        "之后才运行 Jev ablation。原始 `router_eval_v2.jsonl` 未改动。裁决为本次能力审查，未声称独立人工盲审。",
        "",
        "## 判定原则",
        "",
        "- Worldline 要求可解析的 song/artist/album 与证据/关系图；当前没有改变历史的模拟器。",
        "- GeneralChat 可作明确不确定的开放假设讨论，不能以模型记忆声称具体历史因果。",
        "- 请求中若有当前任何子图无法满足的关键事实任务，或特定实体无法从输入/上下文解析，移出计分并保留 negative capability case；不以能回答一小部分代替完整成功。",
        "- `KEEP` 保留原标签，`RELABEL` 改为当前能力可承接的 route set，`REMOVE_FROM_SCORING` 保留 query 但不计入准确率。",
        "",
        "## 15 条 questionable cases 的逐条裁决",
        "",
        "| case_id | query | old_label | capability_analysis | final_label | action | reason |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in decisions:
        values = (row["case_id"], row["query"], ", ".join(row["original_label"]),
                  row["capability_analysis"], ", ".join(row["adjudicated_label"]) or "unsupported",
                  row["action"], row["adjudication_reason"])
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + " |")
    lines += [
        "", "## 冻结集统计", "",
        f"- 历史 47 条：KEEP **{totals['historical_actions']['KEEP']}**，RELABEL **{totals['historical_actions']['RELABEL']}**，REMOVE **{totals['historical_actions']['REMOVE_FROM_SCORING']}**。15 条 questionable 全部明确裁决，无 pending。",
        f"- Gold V2 共 **{totals['total']}** 条：正式 Single **{totals['single_intent']}**，正式 Multi **{totals['multi_intent']}**，不计分 follow-up **{totals['followup_excluded']}**，negative/unsupported **{totals['negative_unsupported']}**。",
        f"- Boundary 标签共 **{totals['boundary']}** 条，其中新增 **{totals['new_paired_boundary']}** 条、7 对。新增对照只比较所需能力，避免把泛音乐知识一律归 Worldline。",
        "- Scored route support（多意图按包含的每条 route 分别计数）："
        + ", ".join(f"{route}={totals['route_support'].get(route, 0)}" for route in ROUTES) + "。",
        "", "## 新增对照与负例", "",
        "| pair_id | case A → route | case B → route |",
        "| --- | --- | --- |",
    ]
    for pair_id, left, right in BOUNDARY_PAIRS:
        lines.append(f"| {pair_id} | {left[1]} → {left[2]} | {right[1]} → {right[2]} |")
    lines += [
        "", "移出计分的历史 case 是 `manual-world-017`、`manual-multi-025`；另纳入 `cap-negative-01`（要求真实反事实历史）与 `cap-negative-02`（票务预订）作为 negative。"
        "这些样本不参与 Choice 单意图准确率；保留它们是为了后续检查强制路由与不支持能力边界。",
        "", "## 复现与冻结", "",
        "运行 `python -m evals.jev_gold_v2` 会从未修改的历史 JSONL、同一 Contract 和冻结裁决表重建数据集与本报告。"
        "每行保留原 case_id、original_label、adjudicated_label、adjudication_reason、capability_contract_version、原始来源 SHA256 和计分状态。"
        "新增边界行没有历史标签，`original_label=[]`；它们在指标中与历史标签修正 cohort 分开报告。",
        "",
    ]
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return REVIEW


if __name__ == "__main__":
    path = freeze_gold()
    print(path)
    rows = read_jsonl(path)
    print(write_review(rows))
    print(json.dumps(summary(rows), ensure_ascii=False, indent=2))
