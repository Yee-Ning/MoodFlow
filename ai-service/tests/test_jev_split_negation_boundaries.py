"""Literal-word boundaries must survive both Jev clause parsers."""

import pytest

from app.agent.decision.jev.router_v4 import split_clauses
from app.agent.decision.jev.router_v5 import RuleGuard
from app.agent.decision.jev.task_orchestrator_v5 import parse_task_clauses

PARSERS = [pytest.param(split_clauses, id="v4"), pytest.param(parse_task_clauses, id="v5")]
ORIGINAL_QUERY = (
    "周杰伦《范特西》与《叶惠美》分别在哪一年发行？"
    "请依据知识库回答，并明确标出支持年份的来源；没有检索证据就直说无法核实。"
)


def assert_grounded(query, clauses):
    assert all(query[item.start:item.end] == item.text for item in clauses)


@pytest.mark.parametrize("parser", PARSERS)
def test_original_rag_question_keeps_year_predicate_and_separate_constraints(parser):
    clauses = parser(ORIGINAL_QUERY)
    assert [item.text for item in clauses] == [
        "周杰伦《范特西》与《叶惠美》分别在哪一年发行",
        "请依据知识库回答",
        "明确标出支持年份的来源",
        "没有检索证据就直说无法核实",
    ]
    assert [(item.start, item.end) for item in clauses] == [(0, 22), (23, 31), (33, 44), (45, 58)]
    assert all(not item.sequential for item in clauses)
    assert tuple(clauses) == RuleGuard().inspect(ORIGINAL_QUERY)
    assert_grounded(ORIGINAL_QUERY, clauses)


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize("query", [
    pytest.param("别人说这首歌好听", id="people"),
    pytest.param("别的要求没有", id="other-requirements"),
    pytest.param("别墅派对歌单", id="villa"),
    pytest.param("别名写在专辑介绍里", id="alias"),
    pytest.param("别称写在歌曲资料里", id="designation"),
    pytest.param("别针主题歌单", id="pin"),
    pytest.param("别样的旋律很动听", id="different-style"),
    pytest.param("别致的编曲很好听", id="delicate"),
    pytest.param("别扭的节奏让我困惑", id="awkward"),
    pytest.param("别院音乐会歌单", id="courtyard"),
    pytest.param("别字需要说明", id="typo"),
    pytest.param("请分别介绍两张专辑", id="separately"),
    pytest.param("特别喜欢这个歌单", id="especially"),
    pytest.param("两张专辑的区别是什么", id="difference"),
    pytest.param("个别歌曲比较安静", id="individual"),
    pytest.param("识别音乐风格很有意思", id="recognition"),
    pytest.param("告别旧歌单让我感伤", id="farewell"),
])
def test_intrinsic_words_survive_with_and_without_following_comma(parser, query):
    for text in (query, query + "，音乐适合聚会"):
        clauses = parser(text)
        assert [item.text for item in clauses] == [text]
        assert tuple(clauses) == RuleGuard().inspect(text)
        assert_grounded(text, clauses)


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize("negative", [
    pytest.param("不要推荐流行歌", id="no-pop"),
    pytest.param("别给我推DJ版", id="no-dj"),
    pytest.param("不要包含live版", id="no-live"),
    pytest.param("别走", id="do-not-leave"),
    pytest.param("不用播放音乐", id="not-needed"),
    pytest.param("无需收藏歌曲", id="unnecessary"),
    pytest.param("不必搜索歌曲", id="need-not"),
    pytest.param("请勿购买机票", id="prohibited"),
    pytest.param("别处理这个歌单", id="do-not-process"),
    pytest.param("别离开播放页", id="do-not-leave-page"),
])
def test_existing_negative_prefixes_still_exclude_cancelled_clauses(parser, negative):
    assert parser(negative) == []
    query = negative + "，解释什么是节拍。"
    clauses = parser(query)
    assert [item.text for item in clauses] == ["解释什么是节拍"]
    assert_grounded(query, clauses)


@pytest.mark.parametrize("parser", PARSERS)
def test_negative_clause_after_punctuation_or_existing_connector_keeps_next_clause(parser):
    for separator in (",", "，", ";", "；", "。", "!", "！", "?", "？", "\n",
                      "然后", "接着", "随后", "顺便", "另外", "同时", "以及", "并且", "并", "最后"):
        query = "解释节拍" + separator + "别播放音乐，说明拍号。"
        clauses = parser(query)
        assert [item.text for item in clauses] == ["解释节拍", "说明拍号"], separator
        assert_grounded(query, clauses)


@pytest.mark.parametrize("parser", PARSERS)
def test_bare_bu_and_mei_are_not_new_negative_prefixes(parser):
    for query in ("不推荐流行歌", "没播放这首歌"):
        assert [item.text for item in parser(query)] == [query]


@pytest.mark.parametrize("parser", PARSERS)
def test_other_negative_prefixes_keep_baseline_matches_after_prefixed_text(parser):
    for negative in ("不要推荐流行歌", "不用播放音乐", "无需收藏歌曲", "不必搜索歌曲", "请勿购买机票"):
        query = "请" + negative + "，解释什么是节拍。"
        clauses = parser(query)
        # Preserve the existing leading text/offsets; only bare 别 gains a boundary.
        assert [item.text for item in clauses] == ["请", "解释什么是节拍"]
        assert_grounded(query, clauses)
