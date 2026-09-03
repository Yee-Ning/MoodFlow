from typing import Any

from app.schemas.music import SearchMusicArgs


SEARCH_MUSIC_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "search_music",
    "description": (
        "Search MoodFlow's real music catalog through its Spring Boot backend. "
        "Call this when the user wants to find a specific song, artist, album, "
        "or other concrete music keyword and real matching tracks are needed. "
        "Do not call it for ordinary conversation, music explanations, or broad "
        "mood/scenario recommendations that have no concrete search target."
    ),
    "parameters": SearchMusicArgs.model_json_schema(),
}

MUSIC_TOOLS = [SEARCH_MUSIC_TOOL]
