import re
from collections.abc import Iterable

from app.agent.foundation.routing import IntentType
from app.agent.foundation.rule_routing.rule_types import RuleSpec


def _patterns(values: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


def _rule(
    name: str,
    intent: IntentType,
    sub_intent: str,
    patterns: tuple[str, ...],
    *,
    priority: int,
    confidence: float,
    exclusions: tuple[str, ...] = (),
) -> RuleSpec:
    return RuleSpec(
        name=name,
        intent=intent,
        sub_intent=sub_intent,
        patterns=_patterns(patterns),
        priority=priority,
        confidence=confidence,
        exclude_patterns=_patterns(exclusions),
    )


_CURRENT_SONG = (
    r"(?:请|麻烦)?(?:帮我)?(?:分析|解析|解读|理解)(?:一下|下)?"
    r"(?:现在这首歌|现在(?:正在)?播放的(?:这)?首歌|当前(?:播放的)?(?:这)?首歌|这首歌|那首歌)"
    r"(?:(?!然后|接着|随后|并且|同时|以及|再(?=帮|请|给|把|告诉|分析|收藏|暂停|播放|继续|恢复|切换|切到|换|下一|上一|查看|打开))[^,，.。!！?？;；]){0,80}",
    r"(?:请|麻烦)?(?:告诉我)?"
    r"(?:现在这首歌|现在(?:正在)?播放的(?:这)?首歌|当前(?:播放的)?(?:这)?首歌|这首歌|那首歌)"
    r"(?:的)?(?:歌词)?(?:到底)?"
    r"(?:表达(?:了)?什么|讲(?:了)?什么|是什么意思|什么意思|有何含义|想说什么)",
    r"(?:请|麻烦)?(?:帮我)?(?:分析|解析|解读|解释|告诉我)?(?:一下)?歌词"
    r"(?:讲(?:了)?什么|表达(?:了)?什么|是什么意思|什么意思|有何含义)",
)

_WORLDLINE_SUBJECT = (
    r"(?:这首(?:歌|歌曲)|当前(?:这)?首歌|现在(?:播放的)?这?首歌|这张专辑|"
    r"周杰伦|林俊杰|陈奕迅|Taylor Swift|Beatles|披头士|"
    r"[\u4e00-\u9fffA-Za-z·. ]{1,16}(?:歌手|乐队|乐团|作曲家|音乐人))"
)
_WORLDLINE_COUNTERFACTUAL = (
    rf"(?:如果|假如|倘若){_WORLDLINE_SUBJECT}[^,.;!?]{{0,16}}?"
    r"(?:从来没有|从未|没有|没)"
    r"(?:发行|发布|创作|写下|演唱|加入|离开|解散|组成|出道|存在)"
    r"[^,.;!?]{0,24}?(?=(?:然后|再(?=帮|请|给|把)|并且|同时|[,.;!?]|$))"
)
_WORLDLINE_EXPLORATION = (
    r"(?:请|麻烦)?(?:帮我)?(?:生成|创建|构建|探索|查看|看看|讲讲|分析)?(?:一下)?"
    r"(?:歌曲|单曲|歌手|音乐人|艺人|乐队|乐团|专辑|唱片)?"
    r"(?:《[^》]{1,100}》|[\u4e00-\u9fffA-Za-z0-9·. '\"-]{1,40})"
    r"(?:的)?(?:音乐)?(?:世界线|world\s*line)(?:图谱)?",
)
_NEGATION = r"(?:别|不要|不用|无需)[^,.;!?]{0,8}"


DEFAULT_RULES: tuple[RuleSpec, ...] = (
    _rule(
        "worldline_entity_exploration",
        IntentType.WORLDLINE,
        "music_entity_worldline",
        _WORLDLINE_EXPLORATION,
        priority=305,
        confidence=0.99,
    ),
    _rule(
        "worldline_strong_counterfactual",
        IntentType.WORLDLINE,
        "music_counterfactual",
        (_WORLDLINE_COUNTERFACTUAL,),
        priority=300,
        confidence=0.99,
    ),
    _rule(
        "pause_music",
        IntentType.TOOL_CALLING,
        "pause_music",
        (
            r"(?<!别)(?<!别先)(?<!不要)(?<!不要先)(?<!不用)(?<!不用先)"
            r"(?:暂停一下|暂停播放|暂停|停一下)",
        ),
        priority=240,
        confidence=0.99,
        exclusions=(rf"{_NEGATION}(?:暂停|停一下)",),
    ),
    _rule(
        "resume_music",
        IntentType.TOOL_CALLING,
        "resume_music",
        (r"(?:继续播放|恢复播放)",),
        priority=240,
        confidence=0.99,
        exclusions=(rf"{_NEGATION}(?:继续播放|恢复播放)",),
    ),
    _rule(
        "next_music",
        IntentType.TOOL_CALLING,
        "next_music",
        (r"(?:播放|切换到|切到|换到|换成|来)?(?:下一首|下一曲)",),
        priority=240,
        confidence=0.99,
        exclusions=(rf"{_NEGATION}(?:下一首|下一曲)",),
    ),
    _rule(
        "previous_music",
        IntentType.TOOL_CALLING,
        "previous_music",
        (r"(?:播放|切换到|切到|换到|换成|来)?(?:上一首|上一曲)",),
        priority=240,
        confidence=0.99,
        exclusions=(rf"{_NEGATION}(?:上一首|上一曲)",),
    ),
    _rule(
        "favorite_song",
        IntentType.TOOL_CALLING,
        "favorite_song",
        (
            r"(?:帮我)?收藏(?:一下)?(?:这首歌|当前(?:播放的)?歌|它)?",
            r"(?:把)?(?:这首歌|当前(?:播放的)?歌|它)(?:标记为)?喜欢",
            r"(?:帮我)?点个喜欢",
        ),
        priority=235,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:收藏|喜欢|点个喜欢)", r"(?:取消|移除)[^,.;!?]{0,8}(?:收藏|喜欢)"),
    ),
    _rule(
        "unfavorite_song", IntentType.TOOL_CALLING, "unfavorite_song",
        (r"(?:取消收藏|取消喜欢|取消红心)(?:这首歌|当前歌曲|当前播放的歌|它)?",
         r"(?:把)?(?:这首歌|当前歌曲|它)(?:的)?(?:红心|收藏|喜欢)取消"),
        priority=250, confidence=0.99,
        exclusions=(rf"{_NEGATION}(?:取消收藏|取消喜欢|取消红心)",
                    rf"{_NEGATION}[^,.;!?]{{0,12}}(?:红心|收藏|喜欢)取消"),
    ),
    _rule(
        "create_playlist", IntentType.TOOL_CALLING, "create_playlist",
        (r"(?:帮我)?(?:新建|创建)(?:一个)?(?:名为|叫)?[^,.;!?]{0,40}歌单[^,.;!?]{0,40}",),
        priority=250, confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:新建|创建)[^,.;!?]{{0,40}}歌单",),
    ),
    _rule(
        "remove_song_from_playlist", IntentType.TOOL_CALLING, "remove_song_from_playlist",
        (r"(?:把|将)?[^,.;!?]{1,50}从[^,.;!?]{0,40}歌单[^,.;!?]{0,8}(?:移除|删除)",
         r"从[^,.;!?]{1,40}歌单[^,.;!?]{0,8}(?:移除|删除)[^,.;!?]{1,40}"),
        priority=250, confidence=0.98,
        exclusions=(rf"{_NEGATION}[^,.;!?]{{0,50}}(?:移除|删除)",),
    ),
    _rule(
        "open_lyrics", IntentType.TOOL_CALLING, "open_lyrics",
        (r"(?:展开|打开|显示)(?:当前歌曲|这首歌)?(?:的)?歌词(?:面板)?",),
        priority=250, confidence=0.99,
        exclusions=(rf"{_NEGATION}(?:展开|打开|显示)[^,.;!?]{{0,8}}歌词",),
    ),
    _rule(
        "clear_queue", IntentType.TOOL_CALLING, "clear_queue",
        (r"清空(?:当前)?(?:播放)?(?:队列|列表)",),
        priority=250, confidence=0.99,
        exclusions=(rf"{_NEGATION}清空",),
    ),
    _rule(
        "jump_to_queue_index", IntentType.TOOL_CALLING, "jump_to_queue_index",
        (r"(?:切到|跳到|切换到)(?:当前)?(?:播放列表|队列)?(?:里(?:的)?)?第\s*(?:[0-9]+|[一二三四五六七八九十百]+)\s*首",),
        priority=250, confidence=0.99,
        exclusions=(rf"{_NEGATION}(?:切到|跳到|切换到)",),
    ),
    _rule(
        "lower_volume",
        IntentType.TOOL_CALLING,
        "lower_volume",
        (
            r"(?:如果)?(?:声音|音量)(?:太|有点|过于)?大(?:了)?(?:就)?"
            r"(?:请|麻烦)?(?:帮我)?(?:调小|降低|降|关小)(?:一点|些)?",
        ),
        priority=235,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:调小|降低|降|关小)",),
    ),
    _rule(
        "get_song_lyrics",
        IntentType.TOOL_CALLING,
        "get_song_lyrics",
        (r"(?:查看|显示|获取|打开)(?:这首歌|当前歌曲|歌曲)?(?:的)?歌词",),
        priority=230,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:查看|显示|获取|打开)[^,.;!?]{{0,8}}歌词",),
    ),
    _rule(
        "queue_insert_next",
        IntentType.TOOL_CALLING,
        "queue_insert_next",
        (
            r"(?:把|将)?[^,.;!?]{1,40}(?:插到|放到|设为|作为)(?:队列)?下一首",
            r"[^,.;!?]{1,40}(?:接下来|下一个)(?:播放|放)",
        ),
        priority=245,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}[^,.;!?]{{0,40}}(?:下一首|接下来|下一个)",),
    ),
    _rule(
        "queue_append",
        IntentType.TOOL_CALLING,
        "queue_append",
        (
            r"(?:把|将)?[^,.;!?]{1,40}(?:加到|加入|添加到|放到)(?:播放)?(?:队列|列表)(?:末尾|最后)?",
            r"[^,.;!?]{1,40}(?:稍后|待会儿?|等会儿?)(?:播放|放)",
        ),
        priority=245,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}[^,.;!?]{{0,40}}(?:队列|列表|稍后|待会|等会)",),
    ),
    _rule(
        "play_collection",
        IntentType.TOOL_CALLING,
        "play_collection",
        (
            r"(?:播放|放)(?:一下)?(?:我的)?[^,.;!?]{1,50}(?:整张|整个|这张)?专辑",
            r"(?:播放|放)(?:一下)?我的[^,.;!?]{1,50}歌单",
        ),
        priority=245,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:播放|放)[^,.;!?]{{0,50}}(?:专辑|歌单)",),
    ),
    _rule(
        "jump_to_queue_item",
        IntentType.TOOL_CALLING,
        "jump_to_queue_item",
        (
            r"(?:切到|跳到|切换到)(?:当前)?(?:队列|播放列表)[^,.;!?]{1,30}",
            r"(?:切到|跳到|切换到)[^,.;!?]{1,30}(?:第\s*\d+\s*首|队列里的)",
        ),
        priority=245,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:切到|跳到|切换到)",),
    ),
    _rule(
        "get_playlist_detail",
        IntentType.TOOL_CALLING,
        "get_playlist_detail",
        (r"(?:查看|看看|看一下)(?:我的)?[^,.;!?]{0,30}歌单[^,.;!?]{0,12}(?:歌|歌曲)",),
        priority=231,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:查看|看看|看一下)[^,.;!?]{{0,40}}歌单",),
    ),
    _rule(
        "get_user_playlists",
        IntentType.TOOL_CALLING,
        "get_user_playlists",
        (r"(?:查看|列出|打开)(?:一下)?我的歌单",),
        priority=230,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:查看|列出|打开)[^,.;!?]{{0,8}}歌单",),
    ),
    _rule(
        "add_song_to_playlist",
        IntentType.TOOL_CALLING,
        "add_song_to_playlist",
        (
            r"(?:把)?(?:这首歌|当前歌曲|它)[^,.;!?]{0,4}"
            r"(?:加入|添加到)(?:我的)?歌单",
        ),
        priority=230,
        confidence=0.98,
        exclusions=(rf"{_NEGATION}(?:加入|添加到)[^,.;!?]{{0,8}}歌单",),
    ),
    _rule(
        "search_music",
        IntentType.TOOL_CALLING,
        "search_music",
        (
            r"(?:帮我)?(?:搜索|查找)(?:一下)?(?:歌曲|歌手|专辑)"
            r"[^,.;!?]{1,40}",
            r"(?:帮我)?(?:搜索|查找)(?:一下)?[^,.;!?]{1,40}",
        ),
        priority=225,
        confidence=0.96,
        exclusions=(rf"{_NEGATION}(?:搜索|查找)",),
    ),
    _rule(
        "analyze_current_song",
        IntentType.CURRENT_SONG,
        "analyze_current_song",
        _CURRENT_SONG,
        priority=200,
        confidence=0.97,
    ),
    _rule(
        "general_greeting",
        IntentType.GENERAL_CHAT,
        "greeting",
        (r"^(?:你好|您好|嗨|哈喽|hello|hi)(?:呀|啊|吗)?[.!?]*$",),
        priority=100,
        confidence=0.99,
    ),
    _rule(
        "general_thanks",
        IntentType.GENERAL_CHAT,
        "thanks",
        (r"^(?:谢谢|感谢你|多谢)(?:啦|了|呀)?[.!?]*$",),
        priority=100,
        confidence=0.99,
    ),
)


def get_default_rules() -> tuple[RuleSpec, ...]:
    return DEFAULT_RULES
