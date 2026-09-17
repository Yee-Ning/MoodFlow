"""Extend the frozen Gold V4 seed with independently reviewed source-only cases.

This script reads the frozen Parent release and the existing 30 Gold lines. It
does not read retrieval traces, rankings, model answers, or network resources.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from app.rag.evaluation.gold_evidence import load_frozen_parents
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import RAGEvaluationCase
from run_rag_gold_v4_audit import validate


ROOT = Path(__file__).resolve().parent
SEED = ROOT / "evals/datasets/rag_eval_gold_v4.candidate.jsonl"
OUT = ROOT / "evals/datasets/rag_eval_gold_v4.expanded_50.candidate.jsonl"
AUDIT = ROOT / "evals/reports/gold_v4_expansion_audit.jsonl"
REPORT = ROOT / "evals/reports/gold_v4_expansion_audit.md"
MANIFEST = ROOT / "data/knowledge/v2/knowledge_release_manifest.json"
SEED_SHA256 = "75dc8910eab396ac7815ec303a126c14dc44b85bf97924f416f1278eee4ee038"


def evidence(parent_id: str, claim: str, start: str, end: str, *, level: str = "full") -> dict:
    return {"parent_id": parent_id, "claim": claim, "start": start, "end": end, "support_level": level}


def item(number: int, query: str, answer: str | None, facts: list[str],
         spans: list[dict] | None = None, *, level: str = "fully_answerable",
         unsupported: list[str] | None = None, forbidden: list[str] | None = None,
         review: str | None = None, note: str = "") -> dict:
    return {"case_id": f"rag-v0-{number:03d}", "query": query, "reference_answer": answer,
            "required_facts": facts, "spans": spans or [], "answerability_level": level,
            "unsupported_claims": unsupported or [], "forbidden_claims": forbidden or [],
            "manual_review_reason": review, "note": note}


SPECS = [
    item(49, "第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？",
         "第十三届金曲奖资料列出：周杰伦凭《爱在西元前》获最佳作曲人奖，方文山凭《威廉古堡》获最佳作词人奖，钟兴民凭《双截棍》获最佳编曲人奖。",
         ["《爱在西元前》的最佳作曲人奖得主是周杰伦", "《威廉古堡》的最佳作词人奖得主是方文山", "《双截棍》的最佳编曲人奖得主是钟兴民"], [
             evidence("parent-2e6c60113d8a12a45bb69d5e", "《爱在西元前》的最佳作曲人奖得主是周杰伦", "流行音樂作品類／最佳作曲人獎 得獎者：周杰倫", "愛在西元前／〔周杰倫范特西〕"),
             evidence("parent-2e6c60113d8a12a45bb69d5e", "《威廉古堡》的最佳作詞人獎得主是方文山", "流行音樂作品類／最佳作詞人獎 得獎者：方文山", "威廉古堡／〔周杰倫范特西〕"),
             evidence("parent-2e6c60113d8a12a45bb69d5e", "《双截棍》的最佳编曲人奖得主是钟兴民", "流行音樂作品類／最佳編曲人獎 得獎者：鍾興民", "雙截棍／(周杰倫范特西)"),
         ], note="仅报告官方获奖表中列出的奖项，不把入围误说成获奖。"),
    item(50, "杰威尔的20周年黑胶套装总计收录多少首曲目？",
         "杰威尔公告称，该黑胶套装总计收录157首曲目。",
         ["20周年黑胶套装合计157首曲目"], [
             evidence("parent-6b75664aa255959035d1af99", "20周年黑胶套装合计157首曲目", "全套黑膠大碟總計收錄了周杰倫157首曲目", "全套黑膠大碟總計收錄了周杰倫157首曲目。"),
         ], note="数字仅限这份20周年黑胶套装，不外推为周杰伦全部作品数。"),
    item(51, "周杰伦20周年黑胶复刻的母带制作和刻片分别由谁负责？",
         "官方公告说，Chris Gehringer 负责黑胶母带音源制作，德国 Optimal Media GmbH 负责 DMM 金属直刻刻板。",
         ["Chris Gehringer负责黑胶母带音源制作", "德国Optimal Media GmbH负责DMM刻板"], [
             evidence("parent-cca31e09c6d05f09267862a1", "Chris Gehringer负责黑胶母带音源制作", "工程師Chris Gehringer負責黑膠母帶音源製作", "工程師Chris Gehringer負責黑膠母帶音源製作"),
             evidence("parent-cca31e09c6d05f09267862a1", "德国Optimal Media GmbH负责DMM刻板", "德國黑膠刻片大廠Optimal Media GmbH", "DMM(金屬直刻)刻板"),
         ], note="仅陈述公告中的制作分工，不推断所有专辑原始录音地点。"),
    item(52, "杰威尔公告称《Mojito》官方YouTube上线前七天达到什么播放量？",
         "杰威尔公告称，《Mojito》官方 YouTube 上线7天突破1000万次观看。",
         ["杰威尔公告称《Mojito》官方YouTube上线7天突破1000万点阅"], [
             evidence("parent-6b75664aa255959035d1af99", "杰威尔公告称《Mojito》官方YouTube上线7天突破1000万点阅", "周杰倫本月推出最新單曲「Mojito」", "官方YouTube上線7天即突破1000萬點閱次數！"),
         ], note="点阅量是公告当时的说法，不作为实时播放量。"),
    item(53, "杰威尔公布的周杰伦珍藏DEMO中，哪首此前未发表？",
         "杰威尔公告称，此前未发表的 DEMO 是《纽约地铁》。",
         ["《纽约地铁》是公告中此前未发表的DEMO"], [
             evidence("parent-0276a6cd536dfaa02e3b39eb", "《纽约地铁》是公告中此前未发表的DEMO", "未發表過的「紐約地鐵」DEMO", "未發表過的「紐約地鐵」DEMO宛如新歌"),
         ], note="不把‘未发表过的DEMO’升级为歌曲此后从未发行。"),
    item(54, "索尼《魔杰座》介绍如何描述《稻香》的曲风与主题？",
         "索尼介绍称，《稻香》是嘻哈民谣搭配周式饶舌，描绘都市人承受生活压力后向往朴实乡村生活。",
         ["索尼称《稻香》结合嘻哈民谣和周式饶舌", "索尼称《稻香》描绘都市压力与返乡向往"], [
             evidence("parent-7a93e3f27e89700594417c6a", "索尼称《稻香》结合嘻哈民谣和周式饶舌", "周杰倫填詞作曲的「稻香｣", "是一首嘻哈民謠搭配周式饒舌曲風"),
             evidence("parent-7a93e3f27e89700594417c6a", "索尼称《稻香》描绘都市压力与返乡向往", "周杰倫填詞作曲的「稻香｣", "回到最初的美好；"),
         ], note="曲风和主题均归于唱片公司文案，不称独立乐评共识。"),
    item(55, "《魔杰座》中《花海》的歌词署名是哪两位？",
         "索尼的专辑介绍记载，《花海》的歌词由古小力、黄凌嘉两人的作品合并而成。",
         ["《花海》的歌词由古小力和黄凌嘉共同完成"], [
             evidence("parent-85c23603441b162614c986e9", "《花海》的歌词由古小力和黄凌嘉共同完成", "這首「花海」，歌詞由兩位杰威爾的同事合力完成", "由古小力和黃凌嘉兩位的歌詞脫穎而出"),
         ], note="两人共同署名作为一个合作关系 claim，不拆成两个重复计分单位。"),
    item(56, "索尼称《喬克叔叔》的创作灵感来自哪部电影？",
         "索尼专辑文案称，周杰伦看过《蝙蝠侠：黑暗骑士》后获得《喬克叔叔》的灵感。",
         ["索尼称《喬克叔叔》的灵感来自电影《蝙蝠侠：黑暗骑士》"], [
             evidence("parent-617c80bffcb611c7aba3f145", "索尼称《喬克叔叔》的灵感来自电影《蝙蝠侠：黑暗骑士》", "這首「喬克叔叔」是周杰倫看的電影「蝙蝠俠-黑暗騎士」", "得到的全新靈感"),
         ], note="创作灵感来自唱片公司描述，不推断导演或MV制作细节。"),
    item(57, "《魔術先生》的编曲是谁，索尼怎样描述其编曲风味？",
         "索尼专辑文案称《魔術先生》是钟兴民的编曲作品，采用带爵士风味的乐团演出方式。",
         ["《魔術先生》的编曲由钟兴民完成", "索尼称《魔術先生》的编曲带有爵士风味"], [
             evidence("parent-617c80bffcb611c7aba3f145", "《魔術先生》的编曲由钟兴民完成", "魔術先生在華麗的美式紐奧良曲風優雅出場", "也是名編曲家鍾興民老師的又一力作！"),
             evidence("parent-617c80bffcb611c7aba3f145", "索尼称《魔術先生》的编曲带有爵士风味", "整首歌的編曲以充滿爵士風味樂團演出的方式呈現", "以充滿爵士風味樂團演出的方式呈現"),
         ], note="编曲者与编曲风味拆为两个 claim；表述为索尼文案。"),
    item(58, "《流浪詩人》由谁作词，采用什么对唱形式？",
         "索尼专辑文案标明《流浪詩人》由方文山作词，周杰伦与杨瑞代以国语、台语对唱。",
         ["《流浪詩人》由方文山作词", "《流浪詩人》是周杰伦与杨瑞代的国台语对唱"], [
             evidence("parent-e1cae0d40bb18a5ee6334ac6", "《流浪詩人》由方文山作词", "流浪詩人 詞 方文山", "流浪詩人 詞 方文山"),
             evidence("parent-e1cae0d40bb18a5ee6334ac6", "《流浪詩人》是周杰伦与杨瑞代的国台语对唱", "這首由周杰倫和楊瑞代國台語混音對唱的藍調小品", "這首由周杰倫和楊瑞代國台語混音對唱的藍調小品"),
         ], note="国台语表述取自专辑文案，不推断具体录音版本。"),
    item(59, "《蘭亭序》的歌词由谁写，索尼称它取材于什么？",
         "索尼文案署名方文山作词，并称歌词取材于书法作品《兰亭序》。",
         ["《蘭亭序》由方文山作词", "索尼称《蘭亭序》取材于同名书法作品"], [
             evidence("parent-e1cae0d40bb18a5ee6334ac6", "《蘭亭序》由方文山作词", "蘭亭序 詞 方文山", "蘭亭序 詞 方文山"),
             evidence("parent-e1cae0d40bb18a5ee6334ac6", "索尼称《蘭亭序》取材于同名书法作品", "方文山繼「菊花台」、「青花瓷」之後", "再以書法極品「蘭亭序」為主題寫中國風！"),
         ], note="只标正文中明示的作词和题材，不推断书法史细节。"),
    item(60, "索尼《魔杰座》文案如何描述《龍戰騎士》的曲风与故事时间设定？",
         "索尼介绍称，《龍戰騎士》采用电子摇滚风格，并设定在一百年后的未来世界。",
         ["索尼称《龍戰騎士》采用电子摇滚风格", "索尼称《龍戰騎士》设定在百年后的世界"], [
             evidence("parent-124172c91dc3903b46ad8310", "索尼称《龍戰騎士》采用电子摇滚风格", "周杰倫以前所未有的電子搖滾風格", "打造出充滿未來世界的奇幻與電影感的「龍戰騎士」"),
             evidence("parent-124172c91dc3903b46ad8310", "索尼称《龍戰騎士》设定在百年后的世界", "這首歌曲設定在100年之後的世界", "這首歌曲設定在100年之後的世界"),
         ], note="‘一百年后’是歌曲叙事设定，不是实际预言。"),
    item(61, "方文山在采访中如何解释用流行歌曲传承传统文化？",
         "方文山认为，可以把中国元素融入流行形式，推向年轻听众，以传承传统文化。",
         ["方文山主张以中国元素结合流行形式面向年轻群体传承传统文化"], [
             evidence("parent-7d0259a9845c71058b642c61", "方文山主张以中国元素结合流行形式面向年轻群体传承传统文化", "面对传统文化的继承，方文山说", "去传承中国的传统文化，这便是其核心价值。"),
         ], note="观点归属方文山，不升级为社会效果已被独立证实。"),
    item(62, "《山水合璧》的歌词融入了哪些《富春山居图》相关素材？",
         "报道说，方文山把画作的流转故事、画家的画风和富春江景色写入《山水合璧》的歌词。",
         ["《山水合璧》歌词融入《富春山居图》故事、画风与富春江景色"], [
             evidence("parent-afca7c2d173dc002cca86aef", "《山水合璧》歌词融入《富春山居图》故事、画风与富春江景色", "作为传世名画《富春山居图》合璧展览的主题曲", "希望这首歌能延长展览的生命力。"),
         ], note="只报道明确写入歌词的三类素材，不推断画作历史细节。"),
    item(63, "环球音乐日本的艺人简介怎样描述2022年《最伟大的作品》的全球专辑销售排名？",
         "环球音乐日本的简介称，按 IFPI 统计，《最伟大的作品》在2022年世界专辑销量排名第一。",
         ["环球音乐日本称《最伟大的作品》在IFPI统计中获2022年全球专辑销量第一"], [
             evidence("parent-1010d70ee8806e5d0e6a3c4f", "环球音乐日本称《最伟大的作品》在IFPI统计中获2022年全球专辑销量第一", "2022年にリリースしたアルバム『最偉大的作品（Greatest Works of Art）』", "世界アルバムセールス1位を獲得"),
         ], note="仅归述唱片公司简介对IFPI的说法，不独立核实IFPI原榜。"),
    item(64, "马来亚大学的《中国风》研究摘要分析了哪两首周杰伦歌曲，使用了哪些研究方法？",
         "该研究摘要写明选取《菊花台》和《千里之外》，并采用文献、乐谱、歌词、听辨、视频分析及虚拟田野等定性方法。",
         ["研究选取《菊花台》和《千里之外》作为分析歌曲", "研究采用多种定性分析方法"], [
             evidence("parent-b7a257305b7fdb3423361194", "研究选取《菊花台》和《千里之外》作为分析歌曲", "The selected songs Chrysanthemums Terrace", "Faraway (千里之外) were analysed."),
             evidence("parent-b7a257305b7fdb3423361194", "研究采用多种定性分析方法", "This study is based on a qualitative research theoretical framework", "virtual fieldwork."),
         ], note="仅陈述研究摘要的样本与方法，不推断论文正文结论。"),
    item(65, "周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？",
         "周杰伦受访时说，很多快歌是看动作片写出来的；冻结资料不足以逐首列出对应的具体电影。",
         ["周杰伦自述很多快歌受到动作片启发", "逐首快歌对应的具体电影清单缺乏证据"], [
             evidence("parent-659d449391ffb5694ba9fdf0", "周杰伦自述很多快歌受到动作片启发", "我的很多快歌都是看动作片写出来的。", "我的很多快歌都是看动作片写出来的。", level="partial"),
         ], level="partially_answerable", unsupported=["每首快歌分别对应哪部具体动作电影"],
         forbidden=["不得从‘看动作片写快歌’反推出《忍者》《双截棍》等各自对应的电影片名"],
         review="逐首电影映射缺失属于全库负证据，需人工复核。"),
    item(66, "方文山与周杰伦合作时通常是先有曲还是先有词？《娘子》的具体分歧发生在哪些词句？",
         "凤凰访谈称，八成以上的合作歌曲先有曲后有词，通常由方文山配合周杰伦；冻结资料不足以说明《娘子》争议的具体词句。",
         ["凤凰访谈称方周合作多数先曲后词", "《娘子》分歧的具体词句缺乏证据"], [
             evidence("parent-fd7a3c5f16d4ad223cd1e529", "凤凰访谈称方周合作多数先曲后词", "合作关系中，八成以上的歌是先有曲再有词", "多是方文山配合周杰伦。", level="partial"),
         ], level="partially_answerable", unsupported=["《娘子》合作分歧对应的具体歌词句子"],
         forbidden=["不得编造《娘子》具体争议词句"],
         review="《娘子》具体分歧缺失属于全库负证据，需人工复核。"),
    item(67, "周杰伦2001年桃园巨蛋首次个人演唱会的完整曲目顺序是什么？",
         "冻结资料不足以核实该场演唱会的完整曲目与顺序，不能列出可靠清单。",
         ["2001年桃园巨蛋首场演唱会完整曲目顺序缺乏可定位证据"], level="insufficient_evidence",
         unsupported=["2001年桃园巨蛋首场个人演唱会的完整曲目顺序"],
         forbidden=["不得把《范特西》专辑曲目顺序当作该场演唱会的演出顺序"],
         review="完整演出曲目缺失属于全库负证据，需人工复核演唱会资料。"),
    item(68, "上一张专辑里的那首歌是谁编曲的？", None, [], level="needs_input",
         note="missing_input: 缺少上一张专辑的参照对象及具体歌曲\nwhy_not_inferable: 当前 query 没有会话上下文；冻结语料含多张专辑和多首歌曲，不能唯一确定指代\nclarification_needed: 请给出专辑名和歌曲名，或提供你所说的上一张专辑的上下文"),
]


def _exact_span(content: str, start: str, end: str, case_id: str) -> str:
    if content.count(start) != 1:
        raise ValueError(f"non-unique/missing span start:{case_id}:{start}")
    left = content.index(start)
    right = content.find(end, left)
    if right >= 0 and right + len(end) < left + len(start):
        right = content.find(end, left + len(start))
    if right < 0:
        raise ValueError(f"missing span end:{case_id}:{end}")
    value = content[left:right + len(end)]
    if not value.strip() or len(value) >= 350 or "![](" in value or "base64," in value:
        raise ValueError(f"non-minimal/noisy span:{case_id}:{start}")
    return value


def build_new_cases(parents: dict) -> tuple[list[RAGEvaluationCase], list[dict]]:
    cases: list[RAGEvaluationCase] = []
    audit: list[dict] = []
    by_content: dict[str, list[str]] = {}
    for parent in parents.values():
        by_content.setdefault(parent.content, []).append(parent.parent_id)
    for spec in SPECS:
        positive = spec["answerability_level"] in ("fully_answerable", "partially_answerable")
        source_spans = []
        for ref in spec["spans"]:
            parent = parents[ref["parent_id"]]
            source_spans.append({
                "parent_id": parent.parent_id,
                "source_id": parent.source_id,
                "exact_text": _exact_span(parent.content, ref["start"], ref["end"], spec["case_id"]),
                "claim": ref["claim"], "support_level": ref["support_level"],
            })
        parent_ids = list(dict.fromkeys(row["parent_id"] for row in source_spans))
        claims = list(dict.fromkeys(row["claim"] for row in source_spans))
        level = spec["answerability_level"]
        groups = []
        for index, row in enumerate(source_spans, start=1):
            alternatives = by_content[parents[row["parent_id"]].content]
            if positive and len(alternatives) > 1:
                groups.append({
                    "evidence_group_id": f"{spec['case_id']}-claim-{index}",
                    "claim": row["claim"], "relation": "duplicate",
                    "acceptable_parent_ids": [row["parent_id"], *sorted(
                        pid for pid in alternatives if pid != row["parent_id"]
                    )],
                })
        outcome = ("answerable" if level == "fully_answerable" else
                   "needs_input" if level == "needs_input" else "insufficient_evidence")
        data = {
            "case_id": spec["case_id"], "query": spec["query"],
            "annotation_status": "pending" if spec["manual_review_reason"] else "approved",
            "expected_outcome": outcome, "answerability_level": None if level == "needs_input" else level,
            "relevant_parent_ids": parent_ids, "supporting_spans": source_spans,
            "evidence_groups": groups, "supported_claims": claims if positive else [],
            "unsupported_claims": spec["unsupported_claims"],
            "required_facts": spec["required_facts"], "forbidden_claims": spec["forbidden_claims"],
            "expected_source_ids": list(dict.fromkeys(parents[pid].source_id for pid in parent_ids)),
            "reference_answer": spec["reference_answer"],
            "tags": ["gold_v4_expansion", "frozen_source_review"] +
                    (["manual_review"] if spec["manual_review_reason"] else []),
            "notes": (spec["note"] + ("\nmanual_review: " + spec["manual_review_reason"]
                                     if spec["manual_review_reason"] else "")).strip(),
        }
        case = RAGEvaluationCase.model_validate(data)
        if positive and (not claims or any(not row["claim"] for row in source_spans)):
            raise ValueError(f"positive case without atomic claims:{case.case_id}")
        cases.append(case)
        audit.append({
            "case_id": case.case_id, "question": case.query, "question_clear": level != "needs_input",
            "answerability_level": level, "expected_outcome": outcome,
            "atomic_claims": case.supported_claims, "unsupported_claims": case.unsupported_claims,
            "supporting_evidence": [{**row, "source_title": parents[row["parent_id"]].source_title}
                                    for row in source_spans],
            "evidence_groups": groups,
            "duplicate_or_equivalent": "claim-scoped exact duplicate groups above; no inferred semantic equivalence",
            "minimal_span_check": "exact frozen Parent substring, under 350 chars, no image/navigation marker",
            "fact_and_uncertainty_check": spec["note"] or (
                "No positive evidence labeled; missing facts require manual review."
                if not positive else "Direct Parent wording only; no unstated inference."
            ),
            "forbidden_claims": case.forbidden_claims,
            "manual_review": bool(spec["manual_review_reason"]),
            "manual_review_reason": spec["manual_review_reason"],
        })
    return cases, audit


def main() -> None:
    seed_bytes = SEED.read_bytes()
    seed_hash = hashlib.sha256(seed_bytes).hexdigest()
    if seed_hash != SEED_SHA256:
        raise ValueError(f"Gold V4 seed changed:{seed_hash}")
    if not seed_bytes.endswith(b"\n"):
        raise ValueError("Gold V4 seed is not newline terminated")
    release, parent_hash, parent_list = load_frozen_parents(MANIFEST)
    parents = {parent.parent_id: parent for parent in parent_list}
    old_cases = load_evaluation_cases(SEED)
    if len(old_cases) != 30:
        raise ValueError("expected exactly 30 frozen seed cases")
    cases, audit = build_new_cases(parents)
    if len(cases) < 20 or len({case.case_id for case in old_cases + cases}) != len(old_cases) + len(cases):
        raise ValueError("insufficient or non-unique expansion cases")
    warnings = validate(cases, parents)
    validate(old_cases + cases, parents)
    payload = seed_bytes + b"".join(
        (json.dumps(case.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for case in cases
    )
    OUT.write_bytes(payload)
    if not OUT.read_bytes().startswith(seed_bytes):
        raise ValueError("seed prefix changed in expanded candidate")
    AUDIT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in audit), encoding="utf-8")
    counts = Counter(spec["answerability_level"] for spec in SPECS)
    lines = [
        "# Gold V4 扩充候选审计", "",
        f"- 冻结 seed：30 条；SHA-256 `{seed_hash}`；新候选前 30 行逐字节相同。",
        f"- 冻结语料：`{release}`；Parent SHA-256 `{parent_hash}`。未使用网络或 Trace。",
        f"- 新增 {len(cases)} 条；总计 {len(old_cases) + len(cases)} 条；full {counts['fully_answerable']}、partial {counts['partially_answerable']}、insufficient {counts['insufficient_evidence']}、needs_input {counts['needs_input']}。",
        f"- 新增 supported claims {sum(len(case.supported_claims) for case in cases)}；unsupported claims {sum(len(case.unsupported_claims) for case in cases)}；evidence groups {sum(len(case.evidence_groups) for case in cases)}。",
        f"- 新增 case validator 通过；warning：{warnings}。负证据 case 保留 manual review，不当作已证明全库不存在。",
        "", "## 逐条核对", "",
    ]
    for case, row in zip(cases, audit, strict=True):
        evidence_descriptions = "; ".join(
            f"{x['parent_id']} ({x['source_id']}，{x['source_title']})：{x['exact_text']}"
            for x in row["supporting_evidence"]
        ) or "无正向证据"
        lines += [f"### {case.case_id}：{case.query}", "",
                  f"- 结论：`{row['answerability_level']}` / `{row['expected_outcome']}`；问题对象明确：{'是' if row['question_clear'] else '否，需补充上下文'}。",
                  f"- 原子 supported claims：{'; '.join(case.supported_claims) or '无'}。",
                  f"- unsupported claims：{'; '.join(case.unsupported_claims) or '无'}。",
                  f"- 最小证据：{evidence_descriptions}",
                  f"- 事实/不确定性：{row['fact_and_uncertainty_check']}；forbidden claims：{'; '.join(case.forbidden_claims) or '无'}。",
                  f"- duplicate/equivalent/complementary：{[(g.evidence_group_id, g.relation, g.acceptable_parent_ids) for g in case.evidence_groups] or '无'}；人工复核：{row['manual_review_reason'] or '否'}。", ""]
    lines += ["## 自动校验边界", "",
              "- Parent ID、source ID、原文子串、span 长度和 claim 一一对应由当前 Gold V4 validator 核验。",
              "- 自动校验不能证明全库负事实或外部来源真实性；含缺失信息判断的 case 保持 pending/manual review。", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"seed": len(old_cases), "new": len(cases), "total": len(old_cases) + len(cases),
                      "answerability": counts, "manual_review": [case.case_id for case in cases
                                                               if case.annotation_status.value == "pending"],
                      "output": str(OUT)}, ensure_ascii=False, default=dict))


if __name__ == "__main__":
    main()
