"""Keep a simple song playback request within its two required tools."""

import re


_PLAY_SONG = re.compile(r"^(?:请|麻烦)?(?:帮我)?(?:播放|放一首|来一首|听一下)\s*\S+")
_OTHER_WORK = re.compile(
    r"[,，。;；]|然后|接着|随后|并|同时|以及|专辑|歌单|歌词|详情|"
    r"收藏|红心|暂停|恢复|队列|音量|分析|探索|世界线|不要|别|不用|无需|当前|这首|正在播放"
)
SONG_PLAYBACK_TOOLS = frozenset({"search_music", "play_song"})


def is_simple_song_playback(text: str) -> bool:
    return bool(_PLAY_SONG.match(text.strip())) and not _OTHER_WORK.search(text)


def scoped_playback_tools(text, tools):
    original = list(tools)
    if not is_simple_song_playback(text):
        return original
    if not SONG_PLAYBACK_TOOLS.issubset({item.name for item in original}):
        return original
    return [item for item in original if item.name in SONG_PLAYBACK_TOOLS]
