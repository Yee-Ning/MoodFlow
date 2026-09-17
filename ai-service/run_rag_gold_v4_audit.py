"""Build and validate an auditable Gold V4 candidate from the frozen V3/source release.

No trace, retrieval result, rank, or score is read when constructing labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.rag.evaluation.gold_evidence import load_frozen_parents
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import RAGEvaluationCase

ROOT = Path(__file__).resolve().parent
V3 = ROOT / "evals/datasets/rag_eval_gold_v3.ai_assisted_source_resolution.jsonl"
V4 = ROOT / "evals/datasets/rag_eval_gold_v4.candidate.jsonl"
CHANGES = ROOT / "evals/reports/gold_v4_changes.jsonl"
MANUAL = ROOT / "evals/reports/gold_v4_manual_review.jsonl"
REPORT = ROOT / "evals/reports/gold_v4_audit_report.md"
MANIFEST = ROOT / "data/knowledge/v2/knowledge_release_manifest.json"
V3_SHA256 = "5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9"

# A sentence in the frozen Parent containing this anchor is shorter and more
# relevant than the 350-character slice in V3. These anchors were reviewed
# against the full Parent, never against historical retrieval ranks.
ANCHORS = {
    ("001", "parent-0276a6cd536dfaa02e3b39eb"): "周杰倫的音樂在創作初期的DEMO",
    ("001", "parent-5ba80a812804e7ee7f9c7008"): "在这一年11月",
    ("002", "parent-cca31e09c6d05f09267862a1"): "2000年11月6日",
    ("002", "parent-0ceb530c507ea8436959d489"): "音乐是一种空间非常大的东西",
    ("002", "parent-bb9810b50e07fbcbb9d00c66"): "《东风破》（2003年）",
    ("002", "parent-944d0185d26660d94bdf1c38"): "在周杰伦的音乐作品中，中国风音乐作品的比重",
    ("003", "parent-396d8237183dea741ebce052"): "送给家人的《外婆》",
    ("005", "parent-0276a6cd536dfaa02e3b39eb"): "由周杰倫挑選的5首DEMO組成",
    ("007", "parent-afca7c2d173dc002cca86aef"): "方文山认为，中国的传统文化",
    ("007", "parent-615f1f589bbe0e801c965b2d"): "《青花瓷》则属于中国风歌曲",
    ("008", "parent-bb9810b50e07fbcbb9d00c66"): "《东风破》（2003年）",
    ("008", "parent-17747bf81bf90411ed3b8a4f"): "〈东风破〉是一首",
    ("023", "parent-a6d0a9714c07f6e028d2425c"): "和周杰伦一起创作出了《东风破》",
    ("023", "parent-fd7a3c5f16d4ad223cd1e529"): "如果没有方文山",
    ("031", "parent-bb9810b50e07fbcbb9d00c66"): "如林俊杰",
    ("034", "parent-4c0b090078529a9643a94d6f"): "A chord progression in his other songs",
    ("036", "parent-cca31e09c6d05f09267862a1"): "2000年11月6日",
    ("036", "parent-17747bf81bf90411ed3b8a4f"): "〈东风破〉是一首",
    ("040", "parent-5ba80a812804e7ee7f9c7008"): "在这一年11月",
    ("043", "parent-19653b37f18f4b5a753a5b6e"): "近些年，周杰伦放缓了发片速度",
    ("043", "parent-396d8237183dea741ebce052"): "在接受CNN采访时",
    ("044", "parent-0276a6cd536dfaa02e3b39eb"): "由周杰倫挑選的5首DEMO組成",
}
DROP = {
    "008": {"parent-a6d0a9714c07f6e028d2425c"},
    "023": {"parent-a5cf1d47acb23747609e6fd1"},
    "036": {"parent-bb9810b50e07fbcbb9d00c66"},
    "038": {"parent-fec4e2f5d74c8fe65d787dd1"},
    "040": {"parent-0276a6cd536dfaa02e3b39eb", "parent-20baa3294417e60fb93bf1df"},
    "042": {"parent-2fb73bd92da464f276c34903"},
    "043": {"parent-bc23e565000bbb36d4ec44e9"},
}
PARTIAL = {"005", "008", "031", "036", "038", "039", "045"}
INSUFFICIENT = {"009", "025", "032", "033", "035"}
REASONS = {
    "024": "问题按作词人举例回答；冻结曲目表证明除方文山外还有周杰伦、黄俊郎，不能声称完整名单。",
    "026": "两组曲目/奖项 Parent 分别存在逐字相同的另一 ID；登记 duplicate group，避免将替代 ID 当检索错误。",
    "034": "和声研究使用 may be identical，保留可能性，缩短截断段落。",
    "036": "2005 Parent 只证明发行日期；无足够证据描述 2000—2005 的完整风格演变，改为部分可证。",
    "038": "阶段论 Parent 把《七里香》及《听妈妈的话》的年表混写，不能作为可靠风格变化证据；仅保留发行事实。",
    "040": "逐个年份核对时间锚点；移除不支持时间线的 DEMO 与重复电影 Parent，新增明确发行证据。",
    "042": "网站导航与托管地址不能作为英文资料内容或作者归属的证据；仅保留英文正文。",
    "044": "将“不要现场版”明确写为禁止输出约束，使用非现场版新闻证据。",
}
MANUAL_REASONS = {
    "003": "中国风兴起与广泛文化影响的概括仍依赖跨来源语义判断。",
    "034": "和声研究的 may be identical 仅是可能性，比较结论需人工复核。",
    "036": "冻结语料没有足以概括 2005 风格演变的直接材料。",
    "038": "剔除错误年表 Parent 后，缺少可靠的《叶惠美》前后风格转变资料。",
    "039": "商业时间线主要由同一 Wikipedia 来源提供，来源可靠性无法仅凭冻结语料确认。",
    "042": "英文资料的作者/机构归属不能凭冻结 Parent 正文确认。",
}

def number(case_id: str) -> str:
    return case_id[-3:]

def sentence(content: str, anchor: str) -> str:
    pos = content.find(anchor)
    if pos < 0:
        raise ValueError(f"missing reviewed anchor: {anchor}")
    left = max(content.rfind(ch, 0, pos) for ch in "。！？\n")
    right_positions = [content.find(ch, pos + len(anchor)) for ch in "。！？\n"]
    right = min((x for x in right_positions if x >= 0), default=len(content) - 1)
    value = content[left + 1:right + 1].strip()
    if not value or len(value) >= 350:
        raise ValueError(f"reviewed sentence too long/empty: {anchor} len={len(value)}")
    return value

def exact_line(content: str, anchor: str) -> str:
    return next(line for line in content.splitlines() if anchor in line).strip()

def span(parent, text: str, *, claim: str | None = None, level: str = "full") -> dict:
    if text not in parent.content:
        raise ValueError(f"span not in frozen Parent: {parent.parent_id}")
    if len(text) >= 350 or not text.strip():
        raise ValueError(f"invalid V4 span boundary: {parent.parent_id}")
    return {"parent_id": parent.parent_id, "exact_text": text,
            "source_id": parent.source_id, "claim": claim, "support_level": level}

def replace_case(data: dict, cid: str, parents: dict) -> None:
    if cid == "024":
        pid = "parent-790e32e522e2ba2f480a77ea"
        p = parents[pid]
        data["relevant_parent_ids"].append(pid)
        for anchor, claim in [
            ("| 3. | 藉口 |", "《藉口》由周杰伦作词"),
            ("| 5. | 将军 |", "《将军》由黄俊郎作词"),
        ]:
            data["supporting_spans"].append(span(p, exact_line(p.content, anchor), claim=claim))
        data["required_facts"] = [
            "方文山是长期合作的作词人；《双截棍》等作品可举例",
            "冻结曲目表另列《藉口》由周杰伦、《将军》由黄俊郎作词",
            "以上是可核实的例子，不是全部歌曲或全部作词人的名单",
        ]
        data["reference_answer"] = "举例来说，方文山是周杰伦长期合作的重要作词人，《双截棍》等歌曲列在两人的合作作品中；《七里香》曲目表还列出《藉口》由周杰伦本人作词、《将军》由黄俊郎作词。这些是冻结语料能核实的例子，不能视为全部作词人名单。"
        data["forbidden_claims"] = ["不得声称周杰伦的所有歌曲都由方文山作词，或声称例子是完整名单"]
    elif cid == "026":
        data["evidence_groups"] = [
            {"evidence_group_id": "026-tracklist", "claim": "《十一月的萧邦》四首曲目的洪敬尧编曲署名",
             "relation": "duplicate", "acceptable_parent_ids": [
                 "parent-cfb9f049a0cfdf86866257fd", "parent-d0c34fdc20c0a8d545deea06"]},
            {"evidence_group_id": "026-award", "claim": "洪敬尧凭《蓝色风暴》获最佳编曲人提名",
             "relation": "duplicate", "acceptable_parent_ids": [
                 "parent-a734651048b7159210d5c4a6", "parent-287a536a7936d114db25a509"]},
        ]
    elif cid == "034":
        data["forbidden_claims"] = ["不得把和声进行 may be identical 写成已证实完全相同"]
    elif cid == "036":
        data["expected_outcome"] = "insufficient_evidence"
        data["required_facts"] = [
            "2000年《Jay》已有多元曲风融合",
            "2003年《东风破》使用二胡、琵琶，是中国风例子",
            "2005年《十一月的萧邦》发行，但发行日期不能证明该年风格如何演变",
        ]
        data["reference_answer"] = "冻结语料可证实2000年《Jay》的多元融合、2003年《东风破》的中国风乐器使用，以及《十一月的萧邦》在2005年的发行；这些节点不足以回答2000到2005年整体音乐风格如何演变。"
        data["forbidden_claims"] = ["不得以2005年专辑发行日期推断2005年的风格变化"]
    elif cid == "038":
        data["expected_outcome"] = "insufficient_evidence"
        data["required_facts"] = ["《叶惠美》于2003年7月31日发行", "剩余可靠证据不足以概括发行前后的风格演变"]
        data["reference_answer"] = "冻结语料可确认《叶惠美》在2003年7月31日发行；用于描述其前后风格变化的阶段论材料含明显年表混淆，不能据此可靠概括风格演变。"
        data["forbidden_claims"] = ["不得引用把《七里香》与《听妈妈的话》年份混写的阶段论作为可靠时间线"]
    elif cid == "039":
        data["expected_outcome"] = "insufficient_evidence"
        data["required_facts"] = [
            "冻结材料记载2011年 DejaVu 餐厅和2012年西安 KTV 两个商业活动节点",
            "资料不足以完整概括2008至2012年的逐年变化；2010年榜单条目还明确标注需要较佳来源",
        ]
        data["reference_answer"] = "冻结语料仅能提供部分节点：同一来源记载2011年 DejaVu 餐厅、2012年西安 KTV 等商业活动；这些记录不能完整回答2008至2012年有哪些变化，2010年榜单条目还带有需更好来源的提示。"
        data["forbidden_claims"] = ["不得把标注需要较佳来源的2010年榜单条目写成已核实事实"]
    elif cid == "040":
        ids = ["parent-cca31e09c6d05f09267862a1", "parent-81d3ee5f0e44759bf67d8c7f",
               "parent-e46f773645df47165d634134", "parent-95160d6644f27840faf6b97d",
               "parent-0ddd0ca8d88f0672b314c240", "parent-a83673d2b967ec913e6b0ef3",
               "parent-531672f53462b6956869e0eb"]
        data["relevant_parent_ids"] = ids
        data["supporting_spans"] = [
            span(parents[ids[0]], sentence(parents[ids[0]].content, "2000年11月6日"), claim="2000年首专发行"),
            span(parents[ids[1]], sentence(parents[ids[1]].content, "2003年7月31日"), claim="2003年《叶惠美》发行"),
            span(parents[ids[2]], sentence(parents[ids[2]].content, "《晴天》在2003年"), claim="2003年《晴天》榜单节点"),
            span(parents[ids[3]], sentence(parents[ids[3]].content, "發行日期2005/11/01"), claim="2005年《十一月的萧邦》发行"),
            span(parents[ids[4]], "2007 我很忙", claim="2007年《我很忙》专辑年表"),
            span(parents[ids[5]], sentence(parents[ids[5]].content, "2007年，周杰倫第一部"), claim="2007年电影上映"),
            span(parents[ids[6]], sentence(parents[ids[6]].content, "2019年9月16日"), claim="2019年《说好不哭》发布"),
        ]
        data["required_facts"] = [
            "2000年首张《JAY》发行",
            "2003年《叶惠美》发行且《晴天》取得当年榜单成绩",
            "2005年《十一月的萧邦》发行",
            "2007年《我很忙》在专辑年表中，电影《不能说的·秘密》上映",
            "2019年《说好不哭》发布并引发高关注",
        ]
        data["reference_answer"] = "可核实的时间线节点包括：2000年11月首张《JAY》发行；2003年7月《叶惠美》发行，《晴天》在当年榜单名列亚军；2005年《十一月的萧邦》发行；2007年专辑年表列有《我很忙》，电影《不能说的·秘密》上映；2019年《说好不哭》发布并引发大量关注。"
        data["forbidden_claims"] = ["不得把仅有发行年份的材料写成该年风格变化的证据"]
    elif cid == "042":
        data["required_facts"] = [
            "冻结英文文本列有专辑《Jay》《Fantasy》的英文标题",
            "另一英文正文记载2000年11月首张《Jay》发行",
        ]
        data["reference_answer"] = "冻结语料中可查到英文资料：英文文本的专辑列表列出 Jay (2000) 和 Fantasy (2001)，另一英文正文记载首张专辑 Jay 于2000年11月发行。"
        data["forbidden_claims"] = ["不得根据网站托管地址推断英文文本的作者或授位机构"]
    elif cid == "044":
        data["forbidden_claims"] = ["不得把现场演出版本当作请求的非现场版新闻资料"]

def finalize_case(data: dict, cid: str, parents: dict) -> None:
    """Second pass after inspecting every candidate span against its claim."""
    def replace_pid(pid: str, texts: list[str], level: str = "full") -> None:
        data["supporting_spans"] = [x for x in data["supporting_spans"] if x["parent_id"] != pid]
        data["supporting_spans"].extend(span(parents[pid], text, level=level) for text in texts)
    demo = "parent-0276a6cd536dfaa02e3b39eb"
    chart = "parent-e46f773645df47165d634134"
    dong = "parent-17747bf81bf90411ed3b8a4f"
    if cid in {"001", "045"}:
        replace_pid(chart, [sentence(parents[chart].content, "《晴天》在2003年")],
                    "partial" if cid == "045" else "full")
    if cid == "001":
        vague = "parent-5ba80a812804e7ee7f9c7008"
        direct = "parent-cca31e09c6d05f09267862a1"
        data["relevant_parent_ids"].remove(vague)
        data["relevant_parent_ids"].append(direct)
        data["supporting_spans"] = [x for x in data["supporting_spans"] if x["parent_id"] != vague]
        data["supporting_spans"].append(span(parents[direct],
            sentence(parents[direct].content, "2000年11月6日"),
            claim="2000年首张《Jay》发行"))
        data["supporting_spans"].append(span(parents[demo],
            sentence(parents[demo].content, "將周杰倫從幕後推向幕前"),
            claim="从幕后走向幕前"))
        data["supporting_spans"].append(span(parents[demo],
            sentence(parents[demo].content, "未經加工的音樂DEMO"),
            claim="DEMO 保留创作初期的画面感"))
        data["reference_answer"] = "冻结资料显示，周杰伦在2000年前后由幕后创作走向幕前，于2000年11月发行首张《JAY》。2003年《叶惠美》中的《晴天》《东风破》进入年度榜单；杰威尔的资料还称其早期 DEMO 完成度较高，并保留创作时的画面感。"
    if cid == "002":
        pid = "parent-bb9810b50e07fbcbb9d00c66"
        data["supporting_spans"].append(span(parents[pid],
            sentence(parents[pid].content, "在音乐上，大多采用传统民族五声调式"),
            claim="中国风的五声音阶和民族乐器与电子音效结合"))
    if cid == "003":
        pid = "parent-bb9810b50e07fbcbb9d00c66"
        data["relevant_parent_ids"].append(pid)
        data["supporting_spans"].append(span(parents[pid],
            sentence(parents[pid].content, "《东风破》（2003年）"),
            claim="《东风破》推动中国风术语活跃"))
        data["required_facts"] = [
            "2000年出道后形成“周杰伦现象”",
            "《东风破》发布后中国风一词在华语乐坛活跃",
            "作品主题涉及爱情、亲情、励志；音乐混合传统和流行元素并影响华语音乐",
        ]
        data["reference_answer"] = "冻结资料记载，周杰伦2000年出道后引起华语乐坛关注并形成“周杰伦现象”；《东风破》发布后，“中国风”一词在华语乐坛活跃。另一英文研究认为其混合风格改变了华语流行音乐场景，人物材料列举了爱情、亲情和励志主题的歌曲。"
    if cid == "003":
        pid = "parent-abd13550e82163b9f04dd8c1"
        text = "2000年出道，隨即引起華語音樂界重大注目，並形成所謂的「周杰倫現象」"
        replace_pid(pid, [text])
    if cid in {"005", "044"}:
        text = sentence(parents[demo].content, "周杰倫的音樂在創作初期的DEMO")
        data["supporting_spans"].append(span(parents[demo], text,
            claim="早期 DEMO 完成度较高", level="partial" if cid == "005" else "full"))
    if cid == "007":
        pid = "parent-afca7c2d173dc002cca86aef"
        replace_pid(pid, ["方文山认为，中国的传统文化具有特殊的文化识别度，比如水墨画、京剧、青花瓷，不用解释，大家一看就知道来自哪个民族。"])
        pid = "parent-615f1f589bbe0e801c965b2d"
        replace_pid(pid, ["《青花瓷》则属于中国风歌曲"])
    if cid in {"008", "036"}:
        text = sentence(parents[dong].content, "这首歌运用了二胡和琵琶")
        data["supporting_spans"].append(span(parents[dong], text,
            claim="《东风破》使用二胡和琵琶", level="partial"))
    if cid == "008":
        data["required_facts"] = [
            "《东风破》由周杰伦演唱、方文山作词并收录于《叶惠美》",
            "《东风破》使用二胡和琵琶，歌名来自曲破",
            "冻结材料未提供其具体创作动机、灵感来源或过程",
        ]
    if cid == "023":
        pid = "parent-fd7a3c5f16d4ad223cd1e529"
        replace_pid(pid, [sentence(parents[pid].content, "八成以上的歌是先有曲再有词")])
        data["reference_answer"] = "冻结材料将周杰伦与方文山描述为长期合作的词曲搭档，并列举《东风破》《菊花台》《千里之外》等共同作品。人物采访转述他们合作中八成以上的歌先有曲再有词，多由方文山配合周杰伦。"
    if cid == "024":
        pid = "parent-a5cf1d47acb23747609e6fd1"
        data["relevant_parent_ids"].remove(pid)
        data["supporting_spans"] = [x for x in data["supporting_spans"] if x["parent_id"] != pid]
        table = "parent-790e32e522e2ba2f480a77ea"
        data["supporting_spans"].append(span(parents[table],
            exact_line(parents[table].content, "| 1. | 我的地盘 |"),
            claim="《我的地盘》由方文山作词"))
        data["required_facts"] = [
            "方文山为《我的地盘》等歌曲作词，早期也与周杰伦合作",
            "《藉口》由周杰伦、《将军》由黄俊郎作词",
            "上述为冻结曲目表的可核实例子，不是全部作词人名单",
        ]
        data["reference_answer"] = "举例来说，《七里香》曲目表列有方文山为《我的地盘》作词、周杰伦为《藉口》作词、黄俊郎为《将军》作词；另有资料记载方文山与周杰伦的早期合作。这些只是冻结语料能核实的例子，不是全部作词人名单。"
    if cid == "026":
        award = "parent-a734651048b7159210d5c4a6"
        replace_pid(award, [exact_line(parents[award].content, "洪敬堯/《藍色風暴》")])
    if cid == "039":
        bad = "parent-09a09073bd75874b3cc5b72a"
        data["relevant_parent_ids"].remove(bad)
        data["supporting_spans"] = [x for x in data["supporting_spans"] if x["parent_id"] != bad]
        for item in data["supporting_spans"]:
            item["support_level"] = "partial"
    if cid == "040":
        sony = "parent-95160d6644f27840faf6b97d"
        replace_pid(sony, [exact_line(parents[sony].content, "發行日期2005/11/01")])
    if cid == "043":
        data["required_facts"] = [
            "新浪直接问答中周杰伦谈电影如何刺激创作灵感",
            "人物文章转述他接受 CNN 采访时关于影响力和责任感的回答，非 CNN 原稿",
        ]
        data["reference_answer"] = "冻结资料中有周杰伦的直接问答，他谈到看电影会刺激创作灵感；另有人物文章转述他在 CNN 采访中关于影响力与责任感的回答，该段不是 CNN 原始采访稿。"
        news = "parent-19653b37f18f4b5a753a5b6e"
        data["relevant_parent_ids"].remove(news)
        data["supporting_spans"] = [x for x in data["supporting_spans"] if x["parent_id"] != news]
    if cid == "044":
        data["required_facts"] = [
            "杰威尔官方新闻提供非现场版 DEMO 项目与创作初期资料",
            "人物报道提供发片节奏变化信息",
            "必须排除现场演出版本",
        ]
        data["reference_answer"] = "可用的非现场版新闻资料包括杰威尔音乐关于周杰伦 DEMO 项目的官方新闻，其中列有《青花瓷》《晴天》等作品并介绍早期 DEMO；另有人物报道提到他的发片节奏变化。"

def validate(cases: list[RAGEvaluationCase], parents: dict) -> list[str]:
    from app.rag.evaluation.v4_answerability import supported_evidence_units

    warnings = []
    groups = set()
    for case in cases:
        cid = number(case.case_id)
        if case.expected_outcome.value == "answerable" and (not case.supporting_spans or case.answerability_level != "fully_answerable"):
            raise ValueError(f"answerable case lacks full evidence: {cid}")
        if case.expected_outcome.value == "insufficient_evidence" and case.answerability_level == "fully_answerable":
            raise ValueError(f"insufficient case mislabeled full: {cid}")
        if case.expected_outcome.value == "insufficient_evidence" and case.supporting_spans and case.answerability_level != "partially_answerable":
            raise ValueError(f"insufficient case has unclassified partial evidence: {cid}")
        for pid in case.relevant_parent_ids:
            if pid not in parents:
                raise ValueError(f"unknown relevant Parent: {cid} {pid}")
        for evidence in case.supporting_spans:
            parent = parents.get(evidence.parent_id)
            if not parent or evidence.exact_text not in parent.content or evidence.source_id != parent.source_id:
                raise ValueError(f"unlocatable evidence: {cid} {evidence.parent_id}")
            if len(evidence.exact_text) >= 350:
                raise ValueError(f"mechanical span length: {cid} {evidence.parent_id}")
            if "base64," in evidence.exact_text or "utm_source" in evidence.exact_text:
                raise ValueError(f"navigation/image in span: {cid} {evidence.parent_id}")
            if evidence.exact_text[0] in "，。；" or evidence.exact_text[-1] in "，：":
                warnings.append(f"{cid}: inspect sentence boundary for {evidence.parent_id}")
            if case.answerability_level in ("fully_answerable", "partially_answerable"):
                if evidence.support_level in ("full", "partial") and not evidence.claim:
                    raise ValueError(f"positive evidence lacks claim: {cid} {evidence.parent_id}")
                if evidence.support_level is None:
                    raise ValueError(f"positive evidence lacks support level: {cid} {evidence.parent_id}")
                if evidence.support_level == "insufficient":
                    warnings.append(f"{cid}: insufficient span excluded from positive evidence: {evidence.parent_id}")
        if set(case.expected_source_ids) != {parents[pid].source_id for pid in case.relevant_parent_ids}:
            raise ValueError(f"source IDs mismatch: {cid}")
        for group in case.evidence_groups:
            key = (cid, group.evidence_group_id)
            if key in groups:
                raise ValueError(f"duplicate group reference: {key}")
            groups.add(key)
            items = [parents.get(pid) for pid in group.acceptable_parent_ids]
            if any(item is None for item in items):
                raise ValueError(f"invalid acceptable Parent: {key}")
            if group.relation == "duplicate" and len({item.content for item in items}) != 1:
                raise ValueError(f"non-identical duplicate group: {key}")
            component_claims = {span.claim for span in case.supporting_spans
                                if span.parent_id in group.acceptable_parent_ids
                                and span.support_level in ("full", "partial")}
            if len(component_claims) > 1:
                warnings.append(f"{cid}: {group.evidence_group_id} bundles {len(component_claims)} component claims")
        if case.answerability_level in ("fully_answerable", "partially_answerable"):
            units = supported_evidence_units(case)
            claims = [unit.claim for unit in units]
            if not units or any(not claim for claim in claims):
                raise ValueError(f"positive evidence unit lacks claim: {cid}")
            if len(claims) != len(set(claims)):
                raise ValueError(f"duplicate scored claim: {cid}")
            if set(case.supported_claims) != set(claims) or len(case.supported_claims) != len(claims):
                raise ValueError(f"supported claims/evidence mismatch: {cid}")
            if case.answerability_level == "partially_answerable":
                if not case.unsupported_claims:
                    raise ValueError(f"partial case lacks unsupported claims: {cid}")
                if set(case.supported_claims) & set(case.unsupported_claims):
                    raise ValueError(f"supported/unsupported claim overlap: {cid}")
            elif case.unsupported_claims:
                raise ValueError(f"full case has unsupported claims: {cid}")
        elif case.supported_claims or case.evidence_groups:
            raise ValueError(f"non-positive case has scored claims/groups: {cid}")
        if case.expected_outcome.value == "needs_input":
            warnings.append(f"{cid}: needs_input requires referent/scope clarification")
        if case.reference_answer and not case.required_facts:
            warnings.append(f"{cid}: answer has no fact mapping")
    warnings.append("Semantic alignment and source reliability remain human-review judgments.")
    return warnings

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    actual = hashlib.sha256(V3.read_bytes()).hexdigest()
    if actual != V3_SHA256:
        raise ValueError(f"Gold V3 changed: {actual}")
    release, parent_sha, parent_list = load_frozen_parents(MANIFEST)
    parents = {item.parent_id: item for item in parent_list}
    before = load_evaluation_cases(V3)
    if args.validate_only:
        after = load_evaluation_cases(V4)
        warnings = validate(after, parents)
        print(json.dumps({"status": "valid", "cases": len(after), "warnings": warnings,
                          "v3_sha256": actual, "parent_sha256": parent_sha}, ensure_ascii=False))
        return
    output = []
    changes = []
    for old in before:
        cid = number(old.case_id)
        data = old.model_dump(mode="json")
        data["answerability_level"] = ("partially_answerable" if cid in PARTIAL else
                                       "insufficient_evidence" if cid in INSUFFICIENT else
                                       None if old.expected_outcome.value == "needs_input" else
                                       "fully_answerable")
        dropped = DROP.get(cid, set())
        data["relevant_parent_ids"] = [pid for pid in data["relevant_parent_ids"] if pid not in dropped]
        spans = []
        for item in data["supporting_spans"]:
            pid = item["parent_id"]
            if pid in dropped:
                continue
            parent = parents[pid]
            anchor = ANCHORS.get((cid, pid))
            value = sentence(parent.content, anchor) if anchor and cid != "034" else item["exact_text"]
            if cid == "034" and pid == "parent-4c0b090078529a9643a94d6f":
                start = parent.content.index("A chord progression in his other songs")
                end = parent.content.index("may be identical.", start) + len("may be identical.")
                value = parent.content[start:end]
            if len(value) >= 350:
                raise ValueError(f"unreviewed 350-character span: {cid} {pid}")
            spans.append(span(parent, value, claim=item.get("claim"),
                              level="partial" if cid in PARTIAL else "full"))
        data["supporting_spans"] = spans
        replace_case(data, cid, parents)
        finalize_case(data, cid, parents)
        data["expected_source_ids"] = list(dict.fromkeys(parents[pid].source_id for pid in data["relevant_parent_ids"]))
        if cid in MANUAL_REASONS:
            data["tags"] = list(dict.fromkeys([*data["tags"], "gold_v4_needs_manual_review"]))
        case = RAGEvaluationCase.model_validate(data)
        output.append(case)
        old_data = old.model_dump(mode="json")
        new_data = case.model_dump(mode="json")
        fields = [key for key in new_data if old_data.get(key) != new_data.get(key)]
        if fields:
            changes.append({
                "case_id": case.case_id, "changed_fields": fields,
                "before_summary": {"outcome": old.expected_outcome.value, "answer": old.reference_answer,
                                   "parents": old.relevant_parent_ids, "span_count": len(old.supporting_spans)},
                "after_summary": {"outcome": case.expected_outcome.value, "answerability_level": case.answerability_level,
                                  "answer": case.reference_answer, "parents": case.relevant_parent_ids,
                                  "span_count": len(case.supporting_spans)},
                "reason": REASONS.get(cid, "将机械截断的证据收紧为冻结 Parent 中的完整句，并显式标注可回答程度。"),
                "evidence_source": {"release": release, "parent_ids": case.relevant_parent_ids},
                "confidence": "medium" if cid in MANUAL_REASONS else "high",
                "requires_manual_review": cid in MANUAL_REASONS,
            })
    warnings = validate(output, parents)
    for path, rows in [(V4, [x.model_dump(mode="json") for x in output]), (CHANGES, changes),
                       (MANUAL, [{"case_id": "rag-v0-" + cid, "reason": reason,
                                  "status": "needs_manual_review"} for cid, reason in MANUAL_REASONS.items()])]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    new_span = {(x.case_id, s.parent_id, s.exact_text) for x in output for s in x.supporting_spans}
    shortened = sum(1 for x in before for s in x.supporting_spans
                    if len(s.exact_text) >= 348 and (x.case_id, s.parent_id, s.exact_text) not in new_span)
    outcomes = Counter(x.expected_outcome.value for x in output)
    report = [
        "# Gold V4 candidate 审计报告", "",
        f"- 冻结 Gold V3 SHA256：`{actual}`；未写入 V3。",
        f"- 冻结来源：`{release}`；Parent artifact `{parent_sha}`。",
        f"- 案例：{len(output)}；outcome 分布：{dict(outcomes)}；变更案例：{len(changes)}。",
        f"- 已替换/移除接近 350 字的 span：{shortened}；V4 span 总数：{sum(len(x.supporting_spans) for x in output)}。",
        f"- forbidden_claims 新增条数：{sum(len(y.forbidden_claims)-len(x.forbidden_claims) for x,y in zip(before,output,strict=True))}。",
        "- `partially_answerable` 是局部事实可证但原问题不能完整回答；为保持既有评测 outcome 合同，expected_outcome 仍为 insufficient_evidence。",
        "- 026 有两组逐字重复的 Parent ID；evidence_groups 仅记录可接受的替代 ID，不按 Trace 倒推标签。旧确定性评测器仍按 relevant_parent_ids 逐 ID 计分，需连同 group-aware 诊断解释结果。",
        "", "## 优先案例", "",
    ]
    for cid in ["024","038","036","040","034","042","044","026"]:
        report.append(f"- {cid}：{REASONS[cid]}")
    report += ["", "## 所有 insufficient_evidence 复核", ""]
    for case in output:
        if case.expected_outcome.value == "insufficient_evidence":
            report.append(f"- {number(case.case_id)}：{case.answerability_level}；{case.reference_answer}")
    report += ["", "## 仍需人工复核", ""]
    for cid, reason in MANUAL_REASONS.items():
        report.append(f"- {cid}：{reason}")
    report += ["", "## 校验与限制", "",
               "- 校验 Parent 存在、来源 ID 一致、span 原文定位、answerable 有证据、partial 与 outcome 对齐、group ID/Parent 有效且 duplicate 文本完全相同、没有 350 字机械截断或图片/导航片段。",
               "- 自动校验不对来源事实真伪和跨句语义作最终判定；需人工复核事项见 manual_review JSONL。",
               "- 指标回放需使用同一历史 Trace 和现有 evaluator；本构建脚本不读取 Trace，也不改检索链路。",
               *[f"- Warning: {item}" for item in warnings], ""]
    REPORT.write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"status": "candidate_created", "case_count": len(output),
                      "changed_case_count": len(changes), "shortened_or_removed_348_plus": shortened,
                      "outcomes": outcomes, "manual_review": list(MANUAL_REASONS),
                      "v3_sha256": actual, "parent_sha256": parent_sha}, ensure_ascii=False, default=dict))

if __name__ == "__main__":
    main()
