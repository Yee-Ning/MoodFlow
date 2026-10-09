"""Explicit scene/expiry boundaries shared by retrieval and profile projection."""

from datetime import UTC, datetime

SCENE_CUES = {
    "workout": ("健身", "跑步", "锻炼", "workout"),
    "sleep": ("睡觉", "睡眠", "助眠", "sleep"),
    "commute": ("通勤", "上班路上", "commute"),
    "study": ("学习", "读书", "study"),
}


def infer_scene(query):
    matches = [scene for scene, cues in SCENE_CUES.items() if any(cue in query.casefold() for cue in cues)]
    return matches[0] if len(matches) == 1 else None


def is_current(valid_until, *, now=None):
    return valid_until is None or valid_until > (now or datetime.now(UTC))


def matches_scene(stored_scene, scene):
    return stored_scene is None or scene is not None and stored_scene == scene
