"""Capability-adjudicated, pre-benchmark Router V3 evaluation corpora."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from app.agent.decision.jev.capability_criteria import CONTRACT_PATH
from evals.jev_shadow_v1 import read_jsonl, write_jsonl

ROOT = Path(__file__).resolve().parents[1]
GOLD_V2 = ROOT / "evals/datasets/router_eval_gold_v2.jsonl"
DEV = ROOT / "evals/datasets/multi_dev_v3.jsonl"
BLIND = ROOT / "evals/datasets/multi_blind_test_v3.jsonl"
SINGLE_BLIND = ROOT / "evals/datasets/single_blind_v3.jsonl"
REPORT = ROOT / "evals/reports/jev_multi_dataset_v3_report.md"
ROUTES = ("current_song", "tool_calling", "general_chat", "worldline")

# case_id, query, gold route set, facet, adjudication note, unsupported/ambiguous
# request part. These are manually authored realistic requests, not production logs.
DEV_NEW = [
    ("d-ct-01", "把此刻播放歌曲的歌词主旨讲清楚，并把它加入我喜欢的歌", ["current_song", "tool_calling"], "current_tool", "当前歌词分析与收藏动作分别需要 SongContext 和播放器命令。", None),
    ("d-ct-02", "切到下一首后，分析新播放曲目的情绪走向", ["tool_calling", "current_song"], "ordered_current_tool", "切歌动作先于新曲目分析；后一任务依赖更新的播放上下文。", None),
    ("d-ct-03", "先查当前播放曲的歌词原文，再解释其中反复出现的意象", ["tool_calling", "current_song"], "ordered_current_tool", "歌词数据获取与歌词意义分析是两个能力。", None),
    ("d-cg-01", "结合正在听的歌说说它的叙事视角；另外解释一下转调是什么", ["current_song", "general_chat"], "current_general", "在播曲目叙事用 SongContext；转调定义是普通音乐知识。", None),
    ("d-cg-02", "谈谈这首在播歌的情绪起伏，接着泛聊慢歌为什么能营造空间感", ["current_song", "general_chat"], "current_general", "具体曲目情绪与通用听感原理是独立任务。", None),
    ("d-cg-03", "请解释切分音，再谈当前歌曲表达的孤独感", ["general_chat", "current_song"], "current_general", "切分音是通用概念；当前歌曲的表达需 SongContext。", None),
    ("d-cw-01", "分析眼前播放歌曲的情绪，再探索 David Bowie 的影响关系与来源", ["current_song", "worldline"], "current_world", "当前曲目情绪与具名艺人证据关系图分别成立。", None),
    ("d-cw-02", "先梳理 Daft Punk 的《Discovery》与其他专辑的有据关系，再谈当前曲目的歌词主题", ["worldline", "current_song"], "ordered_current_world", "具名专辑关系探索和当前曲目歌词主题分属两路。", None),
    ("d-cw-03", "这首在播的歌表达了什么；另画出 Björk 与相关音乐场景的证据图", ["current_song", "worldline"], "current_world", "当前歌分析与具名艺人的 Scene 证据图是独立意图。", None),
    ("d-tg-01", "查找 Massive Attack 的曲目，再解释 trip-hop 与 downtempo 的区别", ["tool_calling", "general_chat"], "tool_general", "目录搜索是工具任务；风格区别是通用解释。", None),
    ("d-tg-02", "音量减半，顺带聊聊节奏为什么会影响运动体验", ["tool_calling", "general_chat"], "tool_general", "调音量是播放器动作；节奏原理是开放讨论。", None),
    ("d-tg-03", "给我打开歌词面板；另说说副歌通常有什么作用", ["tool_calling", "general_chat"], "tool_general", "面板动作与副歌知识说明各需不同能力。", None),
    ("d-tw-01", "搜索《Homogenic》专辑，再探索 Björk 与其他艺人的影响关系并引用来源", ["tool_calling", "worldline"], "tool_world", "目录搜索与有证据的具名艺人关系探索分开。", None),
    ("d-tw-02", "播放 David Bowie 的《Heroes》，然后查他的 Scene 关系和证据", ["tool_calling", "worldline"], "ordered_tool_world", "播放动作与可引用的实体关系图是不同任务。", None),
    ("d-tw-03", "列出 Portishead 的歌，同时生成他们与 Bristol 场景的证据网络", ["tool_calling", "worldline"], "tool_world", "歌曲检索与艺人 Scene 关系证据图分别成立。", None),
    ("d-gw-01", "解释什么是后朋克，另外探索 Joy Division 对哪些乐队有据可查的影响", ["general_chat", "worldline"], "general_world", "风格定义不需图；具名乐队 Influence 探索需要证据。", None),
    ("d-gw-02", "探究 Pink Floyd 的《The Dark Side of the Moon》的影响关系图；再聊聊概念专辑这个概念", ["worldline", "general_chat"], "general_world", "具名专辑影响图与普通概念说明并列。", None),
    ("d-gw-03", "先说迷幻摇滚大概指什么，再列出 Pink Floyd 与音乐场景的可追溯关系", ["general_chat", "worldline"], "general_world", "普通风格解释与具名艺人 Scene 证据探索不同。", None),
    ("d-3-01", "先降低音量，然后分析这首歌的叙事，最后解释什么是桥段", ["tool_calling", "current_song", "general_chat"], "triple_ordered", "播放器动作、当前歌叙事与普通曲式知识均明确。", None),
    ("d-3-02", "查找《Kid A》的歌词，探索 Radiohead 的影响图，并分析当前播放曲目的听感", ["tool_calling", "worldline", "current_song"], "triple", "歌词获取、实体关系图和当前听感分别对应三路。", None),
    ("d-3-03", "查找 Joy Division 的专辑、解释后朋克、探索乐队与场景的关系和来源", ["tool_calling", "general_chat", "worldline"], "triple", "目录查询、通用风格解释与 Scene 证据探索均明确。", None),
    ("d-3-04", "分析当前歌的情绪；解释什么叫音色；再探索 Joni Mitchell 的《Blue》的影响关系和证据", ["current_song", "general_chat", "worldline"], "triple", "当前歌曲、概念解释、具名专辑关系图各需一条路。", None),
    ("d-s-01", "先不要暂停，直接解释什么是律动", ["general_chat"], "single_guard_negation", "明确否定播放器动作，仅有音乐概念解释。", None),
    ("d-s-02", "如果把音量开大，低频会更明显吗？", ["general_chat"], "single_guard_conditional", "条件句讨论一般听感，没有要求执行调音量。", None),
    ("d-s-03", "请不要搜索，只聊聊混音与母带的差别", ["general_chat"], "single_guard_negation", "搜索被否定，只需通用概念解释。", None),
    ("d-s-04", "我只想知道当前歌里反复句的意义，不需要操作播放器", ["current_song"], "single_guard_negation", "否定动作后仍有当前 SongContext 分析意图。", None),
    ("d-s-05", "Radiohead 的影响关系图里有哪些证据来源？", ["worldline"], "single_guard_entity", "只有具名实体的证据图探索；没有独立百科解释。", None),
    ("d-p-01", "把当前歌加入喜欢，同时帮我买下周的演出票", ["tool_calling"], "partial_unsupported", "收藏可由播放器工具承接，购票超出所有 Subgraph。", "ticket_purchase"),
    ("d-p-02", "解释什么是律动，顺便替我预约周五的录音棚", ["general_chat"], "partial_unsupported", "概念解释可答，预约外部场地不受支持。", "studio_booking"),
    ("d-p-03", "搜一下名为《Home》的歌，并探索‘那位同名歌手’的影响网络", ["tool_calling"], "ambiguous_entity", "曲名检索可执行；没有可解析的具名艺人根实体，关系探索需澄清。", "unresolved_artist"),
]

BLIND_NEW = [
    ("b-ct-01", "现在听的曲子副歌在讲什么？讲完后把进度拖到一分半。", ["current_song", "tool_calling"], "ordered_current_tool", "在播歌曲副歌分析与 seek 动作分别需要 SongContext、播放器。", None),
    ("b-ct-02", "把循环模式改成单曲；接着分析正在放的歌为何显得紧张。", ["tool_calling", "current_song"], "current_tool", "循环模式动作与当前歌情绪分析是两路。", None),
    ("b-cg-01", "从眼下歌词看它的情绪转折；另外科普一下十二平均律。", ["current_song", "general_chat"], "current_general", "特定歌词情绪与普通理论解释分开。", None),
    ("b-cg-02", "能谈谈在播歌曲的故事线吗？再泛谈人为什么会怀旧。", ["current_song", "general_chat"], "current_general", "在播曲目叙事与一般心理讨论各需一路。", None),
    ("b-cw-01", "说说我正听的这首歌的叙事，另外追踪 Fleetwood Mac 与其他艺人的影响证据。", ["current_song", "worldline"], "current_world", "当前曲目分析与具名艺人证据关系是不同能力。", None),
    ("b-cw-02", "调查 Björk 的《Vespertine》与哪些作品有关系及出处，再分析眼下播放歌曲的听感。", ["worldline", "current_song"], "current_world", "具名专辑有据关系与当前歌听感分开。", None),
    ("b-tg-01", "在音乐库里找一下 Sade 的曲目，顺便解释 neo-soul 的基本特点。", ["tool_calling", "general_chat"], "tool_general", "目录检索与风格概念说明是两个意图。", None),
    ("b-tg-02", "静音播放器；另聊聊耳机与音箱为何听感不一样。", ["tool_calling", "general_chat"], "tool_general", "静音动作和普遍听感解释分开。", None),
    ("b-tw-01", "检索 Kate Bush 的专辑列表，并整理她与后继歌手的影响边及来源。", ["tool_calling", "worldline"], "tool_world", "专辑列表调用音乐服务；Influence 图需要证据。", None),
    ("b-tw-02", "播放 Fleetwood Mac 的《Dreams》，随后探索该乐队的 Scene 证据链。", ["tool_calling", "worldline"], "ordered_tool_world", "播放与具名艺人 Scene 探索依次成立。", None),
    ("b-gw-01", "解释一下采样在音乐里是什么意思；再查 DJ Shadow 的《Endtroducing.....》影响了哪些作品并列出证据。", ["general_chat", "worldline"], "general_world", "采样定义与具名专辑有据 Influence 探索分开。", None),
    ("b-gw-02", "先梳理 Kraftwerk 的有据影响网络，接着讲讲电子音乐与合成器音乐的区别。", ["worldline", "general_chat"], "general_world", "具名艺人关系图与普通风格比较是两路。", None),
    ("b-3-01", "把声音调到三成；解释这首在播歌的歌词情绪；最后科普节拍器的作用。", ["tool_calling", "current_song", "general_chat"], "triple_ordered", "调音量、在播歌情绪和通用节拍器知识分别成立。", None),
    ("b-3-02", "找出 Kate Bush 的《Hounds of Love》里的歌曲，再做她的影响证据图，顺带解释 Art Pop。", ["tool_calling", "worldline", "general_chat"], "triple", "曲目列表、证据关系和风格概念分别需要工具、图和对话。", None),
    ("b-3-03", "探究 Fleetwood Mac 的关系网与引用，分析当前歌的主题，然后暂停播放。", ["worldline", "current_song", "tool_calling"], "triple_ordered", "实体证据图、当前歌分析和暂停动作均明确。", None),
    ("b-s-01", "不用切歌，我只想了解当前歌曲为什么有压迫感。", ["current_song"], "single_guard_negation", "切歌被否定；仅需要当前歌听感分析。", None),
    ("b-s-02", "如果我搜索 Sade，会找到哪些类型的歌？先不执行搜索，简单解释即可。", ["general_chat"], "single_guard_conditional", "条件提及搜索且明确不执行，只是一般讨论。", None),
    ("b-s-03", "仅说明什么是复调，别动我的播放列表。", ["general_chat"], "single_guard_negation", "歌单动作被否定，仅有概念解释。", None),
    ("b-s-04", "查找 Fleetwood Mac 的《Dreams》的歌词就行，不必分析含义。", ["tool_calling"], "single_guard_negation", "只需歌词服务数据，意义分析被否定。", None),
    ("b-s-05", "只看 Kate Bush 影响了谁的证据，先别播放她的歌。", ["worldline"], "single_guard_negation", "播放器动作被否定；剩下具名艺人有据关系。", None),
    ("b-p-01", "查询 Fleetwood Mac 的歌，再帮我订他们下一场演出的座位。", ["tool_calling"], "partial_unsupported", "目录查询有工具；演出票务不受支持。", "ticket_booking"),
    ("b-p-02", "解释一下节奏型，也帮我发送短信给朋友。", ["general_chat"], "partial_unsupported", "节奏型是普通解释；发送短信无工具。", "sms_sending"),
    ("b-p-03", "暂停播放，再沿着我说的那位重名歌手的 Influence 边走下去。", ["tool_calling"], "ambiguous_entity", "暂停可执行；未解析艺人不能建有据关系图。", "unresolved_artist"),
    ("b-p-04", "分析眼下歌曲的情绪；如果那张没指明的专辑曾提前发行，真实历史会怎样变化？", ["current_song"], "partial_unsupported", "当前歌情绪可分析；不明确专辑的真实反事实史不受支持。", "counterfactual_history"),
]

SINGLE_NEW = [
    ("s-c-01", "在播曲目的第二段歌词比第一段更像在自责吗？", "current_song"),
    ("s-c-02", "我现在听到的副歌，主人公态度有没有转变？", "current_song"),
    ("s-c-03", "从眼下播放的曲子里能读出怎样的叙事视角？", "current_song"),
    ("s-c-04", "只分析当前歌给人的紧绷感从何而来。", "current_song"),
    ("s-c-05", "正在听的这支曲目歌词有哪些重复意象？", "current_song"),
    ("s-t-01", "把播放位置跳到两分十秒。", "tool_calling"),
    ("s-t-02", "从音乐库检索一下 Cocteau Twins 的曲目。", "tool_calling"),
    ("s-t-03", "替我打开当前歌曲的歌词页。", "tool_calling"),
    ("s-t-04", "切换成随机播放模式。", "tool_calling"),
    ("s-t-05", "列出我的个人歌单。", "tool_calling"),
    ("s-g-01", "小调一定听起来比大调难过吗？", "general_chat"),
    ("s-g-02", "复节奏和切分节奏在概念上有何不同？", "general_chat"),
    ("s-g-03", "为什么熟悉的旋律容易被反复播放？", "general_chat"),
    ("s-g-04", "怎样理解歌曲中的留白？", "general_chat"),
    ("s-g-05", "耳朵为什么会觉得某些和弦很稳定？", "general_chat"),
    ("s-w-01", "沿着 Aphex Twin 的有据影响边，看看后继创作者有哪些。", "worldline"),
    ("s-w-02", "给出 Fela Kuti 与相关音乐场景的关系图和引用。", "worldline"),
    ("s-w-03", "追踪 Cocteau Twins 在证据里连接到哪些乐队。", "worldline"),
    ("s-w-04", "梳理 Miles Davis 的影响网络，标出每条边的来源。", "worldline"),
    ("s-w-05", "看看 Joni Mitchell 的《Hejira》与其他作品的有据联系。", "worldline"),
]


def _normal(text: str) -> str:
    return re.sub(r"\W+", "", text, flags=re.UNICODE).lower()


def _new_row(item: tuple, split: str, contract_version: str) -> dict:
    case_id, query, routes, facet, reason, unsupported = item
    return {"case_id": case_id, "query": query, "gold_routes": routes,
            "split": split, "cohort": "true_multi" if len(routes) > 1 else facet,
            "facet": facet, "capability_reason": reason,
            "unroutable_part": unsupported, "source": "manually_authored_capability_v3",
            "capability_contract_version": contract_version,
            "requires_context": "current_song" in routes,
            "has_order_dependency": "ordered" in facet}


def build() -> dict[str, list[dict]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    gold = read_jsonl(GOLD_V2)
    historical = [row for row in gold if row["scoring_status"] == "scored"
                  and row["task"] == "multi_intent"]
    if len(historical) != 11:
        raise ValueError("Gold V2 historical multi seed changed")
    dev = [{"case_id": row["case_id"], "query": row["query"],
            "gold_routes": row["adjudicated_label"], "split": "multi_dev_v3",
            "cohort": "true_multi", "facet": "historical_seed",
            "capability_reason": row["adjudication_reason"], "unroutable_part": None,
            "source": "router_eval_gold_v2", "capability_contract_version": row["capability_contract_version"],
            "requires_context": "current_song" in row["adjudicated_label"],
            "has_order_dependency": "先" in row["query"] or "再" in row["query"]}
           for row in historical]
    dev += [_new_row(item, "multi_dev_v3", contract["version"]) for item in DEV_NEW]
    blind = [_new_row(item, "multi_blind_test_v3", contract["version"])
             for item in BLIND_NEW]
    single = [{"case_id": case_id, "query": query, "gold_routes": [route],
               "split": "single_blind_v3", "source": "manually_authored_capability_v3",
               "capability_contract_version": contract["version"]}
              for case_id, query, route in SINGLE_NEW]
    all_rows = dev + blind + single
    if len({row["case_id"] for row in all_rows}) != len(all_rows):
        raise ValueError("duplicate case ID")
    if any(not row["gold_routes"] or len(row["gold_routes"]) != len(set(row["gold_routes"]))
           or not set(row["gold_routes"]) <= set(contract["route_keys"]) for row in all_rows):
        raise ValueError("invalid capability label")
    prior_queries = {_normal(row["query"]) for row in gold}
    examples = {_normal(example) for spec in contract["routes"]
                for field in ("positive_examples", "negative_examples", "ambiguous_queries")
                for example in spec[field]}
    for row in single:
        if _normal(row["query"]) in prior_queries | examples:
            raise ValueError(f"single blind query overlaps prior examples: {row['case_id']}")
    if len({_normal(row["query"]) for row in all_rows}) != len(all_rows):
        raise ValueError("duplicate query across V3 splits")
    near = [(a["case_id"], b["case_id"], round(SequenceMatcher(None, _normal(a["query"]),
                                                               _normal(b["query"])).ratio(), 3))
            for a in dev for b in blind
            if SequenceMatcher(None, _normal(a["query"]), _normal(b["query"])).ratio() >= .72]
    if near:
        raise ValueError(f"near-template leakage across dev and blind: {near}")
    return {"multi_dev_v3": dev, "multi_blind_test_v3": blind, "single_blind_v3": single}


def freeze() -> dict[str, str]:
    corpora = build()
    paths = {"multi_dev_v3": DEV, "multi_blind_test_v3": BLIND,
             "single_blind_v3": SINGLE_BLIND}
    for name, path in paths.items():
        write_jsonl(path, corpora[name])
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest()
              for name, path in paths.items()}
    dev, blind, single = (corpora[name] for name in paths)
    lines = ["# Jev Multi Router V3 dataset freeze", "",
             "依据 `router_capability_contract.json` v1.0 在任何 V3 预测与阈值搜索前标注。新增请求由本轮人工撰写为真实使用风格，**不是生产日志或独立盲审**；标签没有取自 Router 输出。Gold V2 历史 Multi 11 条仅作为 dev seed，不进入 blind。",
             "", "| Split | 总数 | 真 Multi | 单意图 guard | 部分 unsupported / 实体模糊 |", "| --- | ---: | ---: | ---: | ---: |",
             *[f"| {name} | {len(rows)} | {sum(len(r['gold_routes']) > 1 for r in rows)} | {sum(r.get('facet', '').startswith('single_guard') for r in rows)} | {sum(bool(r.get('unroutable_part')) for r in rows)} |"
               for name, rows in (("multi_dev_v3", dev), ("multi_blind_test_v3", blind), ("single_blind_v3", single))],
             "", "新增 Multi 覆盖六种双路组合与四种三路组合；包含否定动作、条件句、多段自然语言、同义表达、顺序依赖、部分不支持任务和实体模糊。具体请求、逐条能力依据与原 Gold V2 case_id 在 JSONL 中。",
             "", "`gold_routes` 只记录当前子图可承接的独立意图。`unroutable_part` 单独保留未支持或需澄清部分；这类样本的 route-set Exact Match 不等于整句请求已被完成。顺序依赖标记仅供切片分析，Jev 实验只比较 route set，不声称可以替代 Hybrid 任务拆分与依赖图。",
             "", "## 支持度与冻结哈希", "",
             *[f"- {name}: SHA256 `{hashes[name]}`；route support " + ", ".join(f"{route}={Counter(k for r in rows for k in r['gold_routes'])[route]}" for route in ROUTES)
               for name, rows in (("multi_dev_v3", dev), ("multi_blind_test_v3", blind), ("single_blind_v3", single))],
             "", "检查包括原 Gold V2 与 Contract 示例对 Single blind 的精确去重、所有 V3 query 的精确去重，以及 dev/blind 最大规范化字符串相似度 <0.72。语义层面的完全独立性无法由字面检查保证。",
             "", "## 评测边界", "",
             "所有当前歌曲请求假定有有效 SongContext；路由评测不调用下游服务。模糊实体案例按 Contract 需澄清，不强塞给 Worldline。此阶段不会修改原 Gold V2、Contract、Single criteria、生产阈值或生产路由。",
             ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return hashes


if __name__ == "__main__":
    print(json.dumps(freeze(), indent=2))
