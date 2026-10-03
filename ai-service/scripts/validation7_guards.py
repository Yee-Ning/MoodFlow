"""Acceptance-only account, ownership and conservative spending guards."""

import json
import re
from pathlib import Path
from threading import Lock

TEST_USER_ID = "17994646607"
NAME_PATTERN = re.compile(r"^MoodFlowTest-\d{8}-\d{6}(?:-[a-z0-9]+)?$")


def require_test_user(user_id):
    if str(user_id) != TEST_USER_ID:
        raise ValueError("test account identity mismatch")


def require_owned_playlist(playlist_id, owned):
    if type(playlist_id) is not int or playlist_id not in owned:
        raise ValueError("playlist was not created by this acceptance run")


def require_test_name(name):
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError("test playlist name must have a timestamped MoodFlowTest- prefix")


def current_run_playlists(items, owned):
    """Match Spring's actual PlaylistData.id, never a name-prefix guess."""
    return [item for item in items if item["id"] in owned]


class SpendingGate:
    """Reserve peak-price ceilings, retain unknown calls across process exits.

    JEV USD is conservatively converted at 10 CNY/USD for the execution guard;
    that is a safety assumption, not a claimed billing exchange rate.
    """

    def __init__(self, path: Path, *, cap=4.5):
        self.path, self.cap = path, cap
        self.lock = Lock()

    def reserve(self, provider, payload, size):
        if type(size) is not int or size < 0 or size > 128_000:
            raise ValueError("provider request exceeds acceptance byte ceiling")
        if provider == "deepseek":
            if payload.get("model") != "deepseek-flash":
                raise ValueError("unpriced model blocked")
            output = payload.get("max_completion_tokens", payload.get("max_tokens", payload.get("max_output_tokens")))
            if type(output) is not int or not 1 <= output <= 4096:
                raise ValueError("explicit bounded model output required")
            ceiling = (size * 2 + output * 8) / 1_000_000
        elif provider == "typesafe":
            ceiling = size * 0.042 * 10 / 1_000_000
        else:
            raise ValueError("unpriced provider blocked")
        with self.lock:
            data = json.loads(self.path.read_text()) if self.path.exists() else {"reserved_cny": 0, "calls": 0}
            if data["calls"] >= 60 or data["reserved_cny"] + ceiling > self.cap:
                raise ValueError("acceptance spending ceiling reached")
            data["calls"] += 1
            data["reserved_cny"] += ceiling
            temporary = self.path.with_suffix(".new")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(self.path)
            return {"dispatch": data["calls"], "ceiling_cny": ceiling, **data}
