"""A public intent receipt gates writes; uncertain effects are never replayed."""

import json
from contextvars import ContextVar
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from .history_writer import digest, _check_public_text
from .models import ArtifactRecord, HistoryEventType, HistoryPointer
from .public_events import PublicTool
from .execution import ContinuityConflict

active_operations = ContextVar("continuity_operations", default=None)
WRITES = {"create_playlist_tool", "add_song_to_playlist", "remove_song_from_playlist_tool", "set_song_liked_tool"}


def safe_tool_result(name, content):
    from app.schemas.music import (CreatedPlaylistResult, AddSongToPlaylistResult, RemoveSongFromPlaylistResult,
        SongLikeResult, MusicSearchResult, EntitySearchResult, SongLyricsResult, UserPlaylistsResult,
        PlaylistDetailResult, AlbumDetailResult)
    from app.agent.handoff.song_context import SongContextRead

    schemas = {"create_playlist_tool": CreatedPlaylistResult, "add_song_to_playlist": AddSongToPlaylistResult,
               "remove_song_from_playlist_tool": RemoveSongFromPlaylistResult, "set_song_liked_tool": SongLikeResult,
               "search_music": MusicSearchResult, "search_entities_tool": EntitySearchResult,
               "get_song_lyrics": SongLyricsResult, "get_user_playlists": UserPlaylistsResult,
               "get_playlist_detail_tool": PlaylistDetailResult, "get_album_detail_tool": AlbumDetailResult,
               "get_current_song_context": SongContextRead}
    if name not in schemas:
        raise ValueError("tool result has no artifact whitelist")
    value = schemas[name].model_validate_json(content).model_dump(mode="json")
    _check_public_text(value)
    return {"schema_version": 1, "name": name, "result": value}


class OperationJournal:
    def __init__(self, writer, accounting):
        self.writer, self.accounting = writer, accounting

    @property
    def identity(self):
        return self.accounting.identity

    def unknown_writes(self, run_id):
        proposed, completed, cursor = set(), set(), 0
        for kind, target in ((HistoryEventType.TOOL_CALL, proposed), (HistoryEventType.TOOL_RESULT, completed)):
            cursor = 0
            while True:
                rows = self.writer.store.query_task_history(self.identity.continuity_task_id, event_type=kind, after_seq=cursor, limit=100)
                if not rows:
                    break
                for event in rows:
                    body = PublicTool.model_validate(event.payload)
                    if body.run_id == run_id and body.effect == "write":
                        if kind == HistoryEventType.TOOL_CALL or body.status == "success":
                            target.add(body.operation_id)
                    cursor = event.event_seq
        return proposed - completed

    def write_receipts(self, proposal):
        """A namespace change never makes a previously dispatched write new."""
        prior, result = None, None
        for kind in (HistoryEventType.TOOL_CALL, HistoryEventType.TOOL_RESULT):
            cursor = 0
            while True:
                rows = self.writer.store.query_task_history(self.identity.continuity_task_id,
                    event_type=kind, after_seq=cursor, limit=100)
                if not rows:
                    break
                for event in rows:
                    body = PublicTool.model_validate(event.payload)
                    if body.run_id == proposal.run_id and body.operation_id == proposal.operation_id:
                        if (body.name, body.parameters, body.effect) != (proposal.name, proposal.parameters, "write"):
                            raise ContinuityConflict("operation receipt parameters disagree")
                        if kind == HistoryEventType.TOOL_CALL:
                            prior = event
                        elif body.status == "success":
                            result = event
                    cursor = event.event_seq
        return prior, result

    async def call(self, request, execute):
        self.accounting.lease.check()
        call = request.tool_call
        name, parameters = call["name"], call["args"]
        run_id = self.identity.run_id
        from app.agent.foundation.subgraphs.tool_calling.client_tools import CLIENT_ACTION_TOOLS
        action_names = {tool.name for tool in CLIENT_ACTION_TOOLS}
        effect = "write" if name in WRITES else "client_action" if name in action_names else "read"
        # Same logical write gets the same id even if a model invents a new call_id.
        operation_id = "op:" + digest([run_id, name, parameters, "write" if effect == "write" else call["id"]])[7:]
        proposal = PublicTool(run_id=run_id, operation_id=operation_id, name=name, parameters=parameters,
                              status="proposed", effect=effect)
        key = "operation:" + operation_id
        prior, result = self.write_receipts(proposal) if effect == "write" else (None, None)
        if effect == "write" and prior:
            if result and result.payload.get("result_ref"):
                ref = HistoryPointer.model_validate(result.payload["result_ref"])
                artifact = self.writer.store.get_artifact(ref.target_id)
                if artifact is None or artifact.task_id != self.identity.continuity_task_id or digest(artifact.payload) != artifact.content_hash:
                    raise ContinuityConflict("operation result integrity unproven")
                safe = safe_tool_result(name, json.dumps(artifact.payload["result"], ensure_ascii=False))
                return ToolMessage(content=json.dumps(safe["result"], ensure_ascii=False), name=name, tool_call_id=call["id"])
            raise ContinuityConflict("write effect unknown; read-only reconciliation required")
        self.writer.append_public_event(self.identity, key + ":proposed", HistoryEventType.TOOL_CALL, proposal)
        response = await execute(request)
        self.accounting.lease.check()
        success = isinstance(response, ToolMessage) and getattr(response, "status", "success") == "success"
        ref = None
        if success and effect != "client_action":
            try:
                payload = safe_tool_result(name, response.content)
                if effect == "write" and payload["result"].get("status") == "forbidden":
                    raise ValueError("write not accepted")
            except ValueError:
                success = False
            else:
                if len(json.dumps(payload, ensure_ascii=False).encode()) > 256000:
                    raise ContinuityConflict("write result exceeds safe artifact budget")
                artifact_id = digest([operation_id, payload])[7:]
                ref = HistoryPointer(kind="artifact", target_id="artifact:" + artifact_id)
                if self.writer.store.get_artifact(ref.target_id) is None:
                    self.writer.store.put_artifact(ArtifactRecord(artifact_id=artifact_id,
                        task_id=self.identity.continuity_task_id, session_id=self.identity.session_id,
                        artifact_type="public_tool:v1", created_at=datetime.now(UTC), content_hash=digest(payload),
                        payload=payload, projection={"name": name, "operation_id": operation_id}, metadata={}))
        status = "success" if success else "unknown" if effect == "write" else "failed"
        self.writer.append_public_event(self.identity, key + ":" + status, HistoryEventType.TOOL_RESULT,
            proposal.model_copy(update={"status": status, "result_ref": ref}))
        return response


async def journal_tool_call(request, execute):
    operations = active_operations.get()
    return await operations.call(request, execute) if operations is not None else await execute(request)
