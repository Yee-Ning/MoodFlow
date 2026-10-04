"""Prove a requested postcondition with authenticated reads, never repeat a write."""

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from .history_writer import _check_public_text, digest
from .models import ContinuityContract, HistoryPointer


class ReconciliationEvidence(ContinuityContract):
    schema_version: Literal[1] = 1
    operation_id: str
    proposal_ref: HistoryPointer
    checked_at: AwareDatetime
    verdict: Literal["confirmed", "unknown"]
    reads: tuple[str, ...] = Field(default=(), max_length=3)
    observations: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_code: str | None = None


def public_failure(error=None, content=None):
    from app.schemas.music import ToolFailure
    from app.tools.music import MusicServiceError

    code, message = "execution_error", "写请求未取得可确认的成功回执。"
    if isinstance(error, MusicServiceError):
        code, message = error.code, error.public_message
    elif content is not None:
        try:
            parsed = ToolFailure.model_validate_json(content)
            code, message = parsed.error, parsed.message
        except (ValueError, TypeError):
            code = "invalid_write_response"
    values = {"error_code": code[:80], "error_message": message[:1000]}
    try:
        _check_public_text(values)
    except ValueError:
        values = {"error_code": "execution_error", "error_message": "写请求失败，原始错误不适合写入公共历史。"}
    return values


def definite_failure(failure, response=None):
    # These are pre-dispatch request/auth rejections in the Spring API. A 502,
    # timeout, connection loss, conflict or malformed success remains unknown.
    if failure["error_code"] in {"http_400", "http_401", "http_403", "http_422"}:
        return True
    if response is not None:
        import json
        try:
            return json.loads(response.content).get("status") == "forbidden"
        except (ValueError, TypeError, AttributeError):
            pass
    return False


class MusicWriteReconciler:
    """This object has no method that issues POST, PUT, PATCH or DELETE."""

    def __init__(self, client):
        self.client = client

    async def create_baseline(self):
        playlists = await self.client.get_user_playlists()
        return {"playlist_ids": [row.playlist_id for row in playlists.playlists if row.editable],
                "source_hash": digest(playlists.model_dump(mode="json"))}

    async def delete_baseline(self, playlist_id):
        account_id = await self.client.get_account_id()
        detail = await self.client.get_playlist_detail(playlist_id)
        if (detail.playlist_id != playlist_id or detail.creator_id != account_id
                or detail.special_type != 0 or detail.subscribed is not False):
            raise ValueError("delete_ownership_unproven")
        return {"playlist_id": playlist_id, "account_id": account_id,
                "creator_id": detail.creator_id, "special_type": detail.special_type,
                "subscribed": detail.subscribed,
                "source_hash": digest(detail.model_dump(mode="json"))}

    async def inspect(self, proposal, proposal_ref, baseline=None):
        args, name = proposal.parameters, proposal.name
        reads, observations, result, error_code = [], {}, None, None
        try:
            if name in {"add_song_to_playlist", "remove_song_from_playlist_tool"}:
                reads.append("GET /api/user/playlists")
                playlists = await self.client.get_user_playlists()
                if not any(row.playlist_id == args["playlist_id"] and row.editable for row in playlists.playlists):
                    raise ValueError("target_not_owned_or_editable")
                reads.append(f"GET /api/playlists/{args['playlist_id']}")
                detail = await self.client.get_playlist_detail(args["playlist_id"])
                if detail.playlist_id != args["playlist_id"]:
                    raise ValueError("target_mismatch")
                ids = {song.song_id for song in detail.songs}
                present = args["song_id"] in ids
                complete = len(ids) == detail.track_count == len(detail.songs)
                observations = {**args, "present": present, "complete": complete,
                                "track_count": detail.track_count, "returned_count": len(detail.songs),
                                "source_hash": digest(detail.model_dump(mode="json"))}
                added = name == "add_song_to_playlist"
                if (added and present) or (not added and not present and complete):
                    result = {**args, "status": "added" if added else "removed",
                              "message": "原写请求未确认；只读核对已确认歌曲在目标歌单中。" if added
                              else "原写请求未确认；完整歌单读回已确认目标歌曲不存在。"}
            elif name == "set_song_liked_tool":
                reads.append("GET /api/user/liked-songs")
                ids = await self.client.get_liked_song_ids()
                present = args["song_id"] in ids
                observations = {"song_id": args["song_id"], "liked": present,
                                "source_hash": digest(ids)}
                if present == args["liked"]:
                    result = {**args, "status": "synced", "message": "原写请求未确认；只读核对已确认云端红心状态。"}
            elif name == "delete_playlist_tool":
                if (not baseline or baseline.get("playlist_id") != args["playlist_id"]
                        or baseline.get("creator_id") != baseline.get("account_id")
                        or baseline.get("special_type") != 0 or baseline.get("subscribed") is not False):
                    error_code = "delete_baseline_unproven"
                else:
                    reads.append("GET /api/user/profile")
                    account_id = await self.client.get_account_id()
                    if account_id != baseline["account_id"]:
                        error_code = "delete_account_mismatch"
                    else:
                        reads.append("GET /api/user/playlists")
                        playlists = await self.client.get_user_playlists()
                        present = any(row.playlist_id == args["playlist_id"] for row in playlists.playlists)
                        observations = {"playlist_id": args["playlist_id"], "account_id": account_id,
                                        "present": present, "source_hash": digest(playlists.model_dump(mode="json"))}
                        if not present:
                            result = {"playlist_id": args["playlist_id"], "status": "deleted",
                                      "message": "只读核对已确认当前账号自建歌单从自有歌单列表消失。"}
            elif name == "create_playlist_tool":
                reads.append("GET /api/user/playlists")
                playlists = await self.client.get_user_playlists()
                before = set((baseline or {}).get("playlist_ids", []))
                matches = [row for row in playlists.playlists if row.editable and row.name == args["name"]
                           and row.playlist_id not in before]
                observations = {"matching_new_ids": [row.playlist_id for row in matches],
                                "has_baseline": baseline is not None,
                                "source_hash": digest(playlists.model_dump(mode="json"))}
                # The current read DTO omits privacy. Matching a newly observed
                # name/ID does not prove all creation parameters; retain unknown.
                error_code = "creation_privacy_unobservable"
            else:
                error_code = "unsupported_write"
        except Exception as exc:
            error_code = public_failure(error=exc)["error_code"]
        return ReconciliationEvidence(operation_id=proposal.operation_id, proposal_ref=proposal_ref,
            checked_at=datetime.now(UTC), verdict="confirmed" if result else "unknown", reads=tuple(reads),
            observations=observations, result=result, error_code=error_code)


def is_write_status_query(text):
    return isinstance(text, str) and bool(re.search(
        r"op:[0-9a-f]{64}|(?:加上|加进|加入|添加|收藏|红心|喜欢|移除|删除|新建|创建|歌单).{0,18}"
        r"(?:了吗|了没|成功没|有没有|是否|结果[？?]?$|状态[？?]?$)|(?:刚才|刚刚).{0,18}(?:加|收藏|红心|歌单).{0,10}(?:没|吗)",
        text,
    ))


async def answer_write_status_query(text):
    if not is_write_status_query(text):
        return None
    from .operations import active_operations

    operations = active_operations.get()
    if operations is None:
        return "当前没有可查询的写操作账本，无法确认刚才的外部效果；不会据此重复写入。"
    return await operations.answer_query(text)
